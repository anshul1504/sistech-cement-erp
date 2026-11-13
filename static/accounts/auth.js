document.querySelectorAll('[data-password-target]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.passwordTarget);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(show));
    button.setAttribute('aria-label', `${show ? 'Hide' : 'Show'} password`);
    const icon = button.querySelector('use');
    if (icon) icon.setAttribute('href', show ? '#i-eye-off' : '#i-eye');
    input.focus({ preventScroll: true });
  });
});

document.querySelectorAll('input[type=password]').forEach(input => {
  const warning = () => input.closest('form').querySelector('.caps-warning');
  input.addEventListener('keyup', event => {
    const el = warning();
    if (el && event.getModifierState) el.hidden = !event.getModifierState('CapsLock');
  });
  input.addEventListener('blur', () => { const el = warning(); if (el) el.hidden = true; });
});

document.querySelectorAll('.auth-form').forEach(form => form.addEventListener('submit', event => {
  const button = form.querySelector('button[type=submit]');
  if (!button) return;
  if (button.getAttribute('aria-busy') === 'true') { event.preventDefault(); return; }
  if (form.checkValidity()) { button.setAttribute('aria-busy', 'true'); button.disabled = true; }
}));
window.addEventListener('pageshow', () => document.querySelectorAll('[aria-busy=true]').forEach(button => {
  button.disabled = false; button.removeAttribute('aria-busy');
}));

// OTP: keep digits only (handles pasted "123 456" or "Code: 123456"), auto-submit at 6 digits.
document.querySelectorAll('.otp-input').forEach(input => {
  let submitted = false;
  input.addEventListener('input', () => {
    const digits = input.value.replace(/\D/g, '').slice(0, 6);
    if (digits !== input.value) input.value = digits;
    if (digits.length === 6 && !submitted && input.form) {
      submitted = true;
      if (input.form.requestSubmit) input.form.requestSubmit(); else input.form.submit();
    }
    if (digits.length < 6) submitted = false;
  });
});

document.querySelectorAll('[data-resend-seconds]').forEach(button => {
  let remaining = Number(button.dataset.resendSeconds);
  if (!remaining) return;
  const timer = window.setInterval(() => {
    remaining -= 1;
    button.disabled = remaining > 0;
    button.textContent = remaining > 0 ? `Resend code (${remaining}s)` : 'Resend code';
    if (remaining <= 0) window.clearInterval(timer);
  }, 1000);
});
