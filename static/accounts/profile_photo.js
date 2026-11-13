// WhatsApp-style profile photo editor: menu → take/upload → circular crop & zoom → saved with the form.
(() => {
  const root = document.querySelector('[data-pp]');
  if (!root) return;
  const $ = sel => document.querySelector(sel);
  const menu = $('[data-pp-menu]'), status = $('[data-pp-status]'), preview = $('[data-pp-preview]');
  const fileInput = $('[data-pp-file]'), captureInput = $('[data-pp-capture]'), removeBox = $('[data-pp-remove]');
  const cropDlg = $('[data-pp-crop]'), camDlg = $('[data-pp-cam]'), viewDlg = $('[data-pp-view]');
  const canvas = $('[data-pp-canvas]'), ctx = canvas.getContext('2d'), zoom = $('[data-pp-zoom]');
  const video = $('[data-pp-video]'), camMsg = $('[data-pp-cam-msg]');
  const viewItem = menu.querySelector('[data-pp-action="view"]'), removeItem = menu.querySelector('[data-pp-action="remove"]');
  const originalPreview = preview.innerHTML, initial = root.dataset.initial;
  const OUT = 512, MAX_SOURCE = 15 * 1024 * 1024;
  let img = null, scale = 1, base = 1, ox = 0, oy = 0, stream = null, lastUrl = null;

  const setStatus = (html, tone) => { status.innerHTML = html; status.dataset.tone = tone || ''; };
  const openMenu = open => { menu.hidden = !open; root.classList.toggle('is-open', open); };
  const close = dlg => { if (dlg.open) dlg.close(); };

  // ----- menu -----
  root.querySelectorAll('[data-pp-trigger]').forEach(b => b.addEventListener('click', e => { e.stopPropagation(); openMenu(menu.hidden); }));
  document.addEventListener('click', e => { if (!menu.hidden && !menu.contains(e.target)) openMenu(false); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape') openMenu(false); });
  menu.addEventListener('click', e => {
    const action = e.target.closest('[data-pp-action]')?.dataset.ppAction;
    if (!action) return;
    openMenu(false);
    if (action === 'upload') fileInput.click();
    if (action === 'camera') openCamera();
    if (action === 'view') { const src = preview.querySelector('img')?.src; if (src) { $('[data-pp-full]').src = src; viewDlg.showModal(); } }
    if (action === 'remove') removePhoto();
  });
  document.querySelectorAll('[data-pp-close]').forEach(b => b.addEventListener('click', () => close(b.closest('dialog'))));
  [cropDlg, camDlg, viewDlg].forEach(d => d.addEventListener('click', e => { if (e.target === d) d.close(); }));

  // ----- choose a file (upload or mobile camera) -----
  const loadFile = file => {
    if (!file) return;
    if (!/^image\//.test(file.type)) { setStatus('Please choose an image file.', 'error'); return; }
    if (file.size > MAX_SOURCE) { setStatus('That image is larger than 15 MB. Please choose a smaller one.', 'error'); return; }
    const url = URL.createObjectURL(file);
    const image = new Image();
    image.onload = () => openCrop(image);
    image.onerror = () => setStatus('This image could not be read. Try a PNG or JPG.', 'error');
    image.src = url;
  };
  fileInput.addEventListener('change', () => { const f = fileInput.files[0]; fileInput.value = ''; loadFile(f); });
  captureInput.addEventListener('change', () => { const f = captureInput.files[0]; captureInput.value = ''; loadFile(f); });

  // ----- camera (desktop + modern mobile); falls back to the phone's camera picker -----
  async function openCamera() {
    if (!navigator.mediaDevices?.getUserMedia) { captureInput.click(); return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'user', width: { ideal: 1280 } }, audio: false });
      video.srcObject = stream; await video.play();
      camMsg.textContent = 'Look at the camera and press the shutter.';
      camDlg.showModal();
    } catch (err) {
      setStatus('Camera not available or permission denied. You can upload a photo instead.', 'error');
      captureInput.click();
    }
  }
  const stopCamera = () => { stream?.getTracks().forEach(t => t.stop()); stream = null; video.srcObject = null; };
  camDlg.addEventListener('close', stopCamera);
  $('[data-pp-shoot]').addEventListener('click', () => {
    const shot = document.createElement('canvas');
    shot.width = video.videoWidth; shot.height = video.videoHeight;
    const c = shot.getContext('2d'); c.translate(shot.width, 0); c.scale(-1, 1); // mirror like a selfie preview
    c.drawImage(video, 0, 0);
    camDlg.close();
    const image = new Image(); image.onload = () => openCrop(image); image.src = shot.toDataURL('image/jpeg', .92);
  });

  // ----- crop & zoom -----
  function openCrop(image) {
    img = image;
    base = Math.max(canvas.width / img.width, canvas.height / img.height);
    scale = 1; ox = 0; oy = 0; zoom.value = 1;
    draw(); cropDlg.showModal();
  }
  function clamp() {
    const w = img.width * base * scale, h = img.height * base * scale;
    const maxX = Math.max(0, (w - canvas.width) / 2), maxY = Math.max(0, (h - canvas.height) / 2);
    ox = Math.min(maxX, Math.max(-maxX, ox)); oy = Math.min(maxY, Math.max(-maxY, oy));
  }
  function render(target, size) {
    const k = size / canvas.width, w = img.width * base * scale * k, h = img.height * base * scale * k;
    target.fillStyle = '#fff'; target.fillRect(0, 0, size, size);
    target.drawImage(img, (size - w) / 2 + ox * k, (size - h) / 2 + oy * k, w, h);
  }
  function draw() { clamp(); render(ctx, canvas.width); }
  zoom.addEventListener('input', () => { scale = parseFloat(zoom.value); draw(); });
  let drag = null;
  canvas.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, ox, oy }; canvas.setPointerCapture(e.pointerId); });
  canvas.addEventListener('pointermove', e => {
    if (!drag) return;
    const ratio = canvas.width / canvas.getBoundingClientRect().width;
    ox = drag.ox + (e.clientX - drag.x) * ratio; oy = drag.oy + (e.clientY - drag.y) * ratio; draw();
  });
  canvas.addEventListener('pointerup', () => { drag = null; });
  canvas.addEventListener('wheel', e => { e.preventDefault(); scale = Math.min(4, Math.max(1, scale - e.deltaY * .002)); zoom.value = scale; draw(); }, { passive: false });

  $('[data-pp-done]').addEventListener('click', () => {
    const out = document.createElement('canvas'); out.width = out.height = OUT;
    render(out.getContext('2d'), OUT);
    out.toBlob(blob => {
      const file = new File([blob], 'profile-photo.jpg', { type: 'image/jpeg' });
      try { const dt = new DataTransfer(); dt.items.add(file); fileInput.files = dt.files; }
      catch (e) { setStatus('Your browser cannot attach the edited photo. Please use a recent browser.', 'error'); return; }
      if (lastUrl) URL.revokeObjectURL(lastUrl);
      lastUrl = URL.createObjectURL(blob);
      preview.innerHTML = `<img src="${lastUrl}" alt="">`;
      removeBox.checked = false; viewItem.hidden = false; removeItem.hidden = false;
      setStatus('New photo ready. Press <strong>Save profile</strong> to apply it.', 'ok');
      cropDlg.close();
    }, 'image/jpeg', .9);
  });

  // ----- remove with undo -----
  function removePhoto() {
    fileInput.value = ''; removeBox.checked = true;
    preview.innerHTML = `<span class="pp-initial">${initial}</span>`;
    viewItem.hidden = true; removeItem.hidden = true;
    setStatus('Photo will be removed when you press <strong>Save profile</strong>. <button type="button" class="pp-undo" data-pp-undo>Undo</button>', 'warn');
    status.querySelector('[data-pp-undo]').addEventListener('click', () => {
      removeBox.checked = false; preview.innerHTML = originalPreview;
      const has = !!preview.querySelector('img'); viewItem.hidden = !has; removeItem.hidden = !has;
      setStatus('Tap the photo or camera to change it.');
    });
  }
})();
