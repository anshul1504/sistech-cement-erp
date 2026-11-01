document.querySelectorAll('[data-password-target]').forEach(button => {
  button.addEventListener('click', () => {
    const input = document.getElementById(button.dataset.passwordTarget);
    const show = input.type === 'password';
    input.type = show ? 'text' : 'password';
    button.setAttribute('aria-pressed', String(show));
    button.setAttribute('aria-label', `${show ? 'Hide' : 'Show'} password`);
    input.focus();
  });
});
document.querySelectorAll('input[type=password]').forEach(input => {
  input.addEventListener('keyup', event => {
    const warning = input.closest('form').querySelector('.caps-warning');
    if (warning) warning.hidden = !event.getModifierState('CapsLock');
  });
  input.addEventListener('blur', () => {
    const warning = input.closest('form').querySelector('.caps-warning');
    if (warning) warning.hidden = true;
  });
});
document.querySelectorAll('.auth-form').forEach(form => form.addEventListener('submit', () => {
  const button = form.querySelector('button[type=submit]');
  if (button && form.checkValidity()) { button.disabled = true; button.setAttribute('aria-busy', 'true'); }
}));
window.addEventListener('pageshow', () => document.querySelectorAll('[aria-busy=true]').forEach(button => {
  button.disabled = false; button.removeAttribute('aria-busy');
}));
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
