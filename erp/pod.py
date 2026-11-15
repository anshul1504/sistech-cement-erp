"""Independent, source-bound POD corrections. Operational evidence never edits ledger history."""
import hashlib
import json
import uuid
from decimal import Decimal,InvalidOperation
from django.core.exceptions import PermissionDenied,ValidationError
from django.db import transaction
from django.utils import timezone
from accounts.security import access_allowed
from accounts.delegation import authorize,can_approve
from .models import Dispatch,PODCorrection,AuditEvent
from .services import require_site
from . import logistics as L

FIELDS=('received_by','receiver_mobile','shortage_qty','damaged_qty','pod_notes')


def details(dispatch,received_by,receiver_mobile='',shortage=0,damaged=0,notes=''):
    receiver=(received_by or '').strip();notes=(notes or '').strip()
    mobile=(receiver_mobile or '').strip().replace(' ','').replace('-','')
    if mobile.startswith('+91'):mobile=mobile[3:]
    elif len(mobile)==12 and mobile.startswith('91'):mobile=mobile[2:]
    elif len(mobile)==11 and mobile.startswith('0'):mobile=mobile[1:]
    if not receiver or len(receiver)>80:raise ValidationError('Enter a receiver name of up to 80 characters.')
    if len(notes)>200:raise ValidationError('Delivery notes must fit 200 characters.')
    if mobile and (len(mobile)!=10 or not mobile.isascii() or not mobile.isdecimal()):raise ValidationError('Enter a ten-digit receiver mobile number or leave it blank.')
    values=[]
    for field,raw in [('shortage_qty',shortage),('damaged_qty',damaged)]:
        try:
            value=Decimal(str(raw or 0))
            if not value.is_finite():raise InvalidOperation
            Dispatch._meta.get_field(field).clean(value,dispatch)
        except (InvalidOperation,TypeError,ValueError,ValidationError):raise ValidationError('Shortage and damage must be finite non-negative quantities with at most three decimal places.') from None
        if value<0:raise ValidationError('Shortage and damage cannot be negative.')
        values.append(value.quantize(Decimal('.001')))
    loaded=sum((line.quantity for line in dispatch.lines.all()),Decimal(0))
    if sum(values)>loaded:raise ValidationError(f'Shortage + damaged cannot be more than the {loaded} units loaded.')
    if any(values) and not notes:raise ValidationError('Describe the shortage / damage — it is used for the claim.')
    return dict(received_by=receiver,receiver_mobile=mobile,shortage_qty=str(values[0]),damaged_qty=str(values[1]),pod_notes=notes)


def validate_file(file):
    if not file:return None
    try:data,kind=file
    except (ValueError,TypeError):raise ValidationError('Supply a supported POD file and content type.') from None
    if not isinstance(data,(bytes,bytearray,memoryview)):raise ValidationError('Supply POD file bytes.')
    data=bytes(data)
    supported={'application/pdf':data.startswith(b'%PDF'),'image/png':data.startswith(b'\x89PNG'),
        'image/jpeg':data.startswith(b'\xff\xd8\xff'),'image/webp':data.startswith(b'RIFF') and data[8:12]==b'WEBP'}
    if len(data)>5*1024*1024 or not supported.get(kind):raise ValidationError('POD file must be a PDF, PNG, JPEG or WebP up to 5 MB.')
    return data,kind


def snapshot(dispatch):
    evidence={name:str(getattr(dispatch,name)) for name in FIELDS}
    evidence.update(status=dispatch.status,delivered_at=dispatch.delivered_at.isoformat() if dispatch.delivered_at else '',
        recorded_by=dispatch.pod_recorded_by_id,file_sha256=hashlib.sha256(bytes(dispatch.pod_file or b'')).hexdigest(),file_type=dispatch.pod_file_type,
        loaded=[(row.pk,str(row.quantity)) for row in dispatch.lines.order_by('pk')],
        approved_corrections=list(dispatch.pod_corrections.filter(status='approved').order_by('pk').values_list('pk',flat=True)))
    return evidence


def digest(evidence):return hashlib.sha256(json.dumps(evidence,sort_keys=True).encode()).hexdigest()


def makers(item):
    d=item.dispatch
    people={item.created_by_id,d.created_by_id,d.pod_recorded_by_id}
    people.update(d.pod_corrections.filter(status='approved').values_list('created_by_id',flat=True))
    # Retained historical audit actors cover legacy PODs without explicit recorder attribution.
    people.update(AuditEvent.objects.filter(site=d.site,reference=d.number,action='Delivered (POD)').values_list('actor_id',flat=True))
    return people-{None}


def _read(user,dispatch):
    if not access_allowed(user) or not user.has_perm('erp.view_dispatch'):raise PermissionDenied
    require_site(user,dispatch.site)


def can_decide(user,item):
    try:_read(user,item.dispatch)
    except PermissionDenied:return False
    return item.status=='requested' and user.pk not in makers(item) and can_approve(user,'approve_pod_correction',item.dispatch.site,makers=makers(item))


@transaction.atomic
def propose(user,pk,values,reason,key,expected_digest):
    d=L._lock(pk,user);_read(user,d);L._need(user,'pod_dispatch');L._state(d,'delivered')
    after=details(d,values.get('received_by'),values.get('receiver_mobile'),values.get('shortage'),values.get('damaged'),values.get('notes'))
    reason=(reason or '').strip()
    if not reason or len(reason)>300:raise ValidationError('Record a correction reason of up to 300 characters.')
    try:key=uuid.UUID(str(key))
    except (ValueError,TypeError,AttributeError):raise ValidationError('Open a fresh delivery correction form.') from None
    old=PODCorrection.objects.filter(request_key=key).first()
    if old:
        if (old.dispatch_id,old.created_by_id,old.after,old.reason)!=(d.pk,user.pk,after,reason):raise ValidationError('This request key belongs to a different correction.')
        return old
    before=snapshot(d)
    if expected_digest!=digest(before):raise ValidationError('Delivery evidence changed. Reopen the trip and review its current POD.')
    if all(before[name]==after[name] for name in FIELDS):raise ValidationError('Change at least one delivery detail before requesting correction.')
    if d.pod_corrections.filter(status='requested').exists():raise ValidationError('A POD correction is already pending.')
    item=PODCorrection(dispatch=d,before=before,after=after,source_digest=digest(before),reason=reason,request_key=key,created_by=user)
    item.full_clean();item.save();L._log(user,d,'POD correction requested',f'{item.pk} · {reason}')
    return item


@transaction.atomic
def decide(user,pk,action,note):
    header=PODCorrection.objects.only('dispatch_id').get(pk=pk);d=L._lock(header.dispatch_id,user);_read(user,d)
    item=PODCorrection.objects.select_for_update().select_related('dispatch__site').get(pk=pk)
    if item.status!='requested':raise ValidationError('This POD correction is already decided.')
    note=(note or '').strip()
    if not note or len(note)>300:raise ValidationError('Record a finding of up to 300 characters.')
    if action=='cancel':
        if user.pk!=item.created_by_id and not user.is_superuser:raise PermissionDenied
    elif action in ['approve','reject']:
        if user.pk in makers(item):raise ValidationError('A POD contributor cannot review their own correction (maker-checker).')
        authorize(user,'approve_pod_correction',d.site,makers=makers(item),reference=f'PODCOR-{item.pk:05d}')
    else:raise ValidationError('Choose approve, reject or cancel.')
    if action=='approve':
        L._state(d,'delivered')
        if digest(snapshot(d))!=item.source_digest:raise ValidationError('Delivery evidence changed; reject and request a fresh correction.')
        after=details(d,item.after['received_by'],item.after['receiver_mobile'],item.after['shortage_qty'],item.after['damaged_qty'],item.after['pod_notes'])
        for field,value in after.items():setattr(d,field,Decimal(value) if field in ['shortage_qty','damaged_qty'] else value)
        d.save(update_fields=FIELDS)
    item.status={'approve':'approved','reject':'rejected','cancel':'cancelled'}[action]
    item.reviewed_by=user;item.reviewed_at=timezone.now();item.review_note=note;item.full_clean();item.save()
    L._log(user,d,'POD correction '+item.status,f'{item.pk} · {note}')
    return item
