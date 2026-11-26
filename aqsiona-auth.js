/* Aqsiona · cuentas de clientes (Supabase Auth, sin librerías externas) */
window.AQAuth = (function () {
  var BASE = 'https://swshjvpdmhoglanjazjn.supabase.co';
  var KEY = 'sb_publishable_ulXMSk8S8gb137qEd0FHoQ_jZfDISl8';
  var SK = 'aq_session';

  function hdr(token) {
    var h = { 'apikey': KEY, 'Content-Type': 'application/json' };
    if (token) h['Authorization'] = 'Bearer ' + token;
    return h;
  }
  function now() { return Math.floor(Date.now() / 1000); }
  function save(d) {
    var s = {
      access_token: d.access_token,
      refresh_token: d.refresh_token,
      expires_at: d.expires_at || now() + (Number(d.expires_in) || 3600),
      user: d.user || null
    };
    try { localStorage.setItem(SK, JSON.stringify(s)); } catch (e) {}
    return s;
  }
  function load() { try { return JSON.parse(localStorage.getItem(SK)); } catch (e) { return null; } }
  function clear() { try { localStorage.removeItem(SK); } catch (e) {} }

  async function req(path, opt) {
    var r;
    try { r = await fetch(BASE + path, opt); }
    catch (e) { var ne = new Error('network'); ne.code = 'network'; throw ne; }
    var j = null;
    try { j = await r.json(); } catch (e) {}
    if (!r.ok) {
      var err = new Error((j && (j.error_description || j.msg || j.message || j.error)) || ('HTTP ' + r.status));
      err.code = (j && (j.error_code || j.error)) || '';
      err.status = r.status;
      throw err;
    }
    return j;
  }

  /* Mensaje legible para el visitante */
  function message(err, en) {
    var c = (err && err.code) || '', st = err && err.status;
    if (c === 'network') return en ? 'We could not connect. Please try again.' : 'No pudimos conectar. Probá de nuevo.';
    if (c === 'invalid_credentials' || c === 'invalid_grant') return en ? 'Incorrect email or password.' : 'Email o contraseña incorrectos.';
    if (c === 'email_not_confirmed') return en ? 'Your account has not been confirmed yet.' : 'Tu cuenta todavía no está confirmada.';
    if (c === 'weak_password') return en ? 'The password is too weak. Use at least 8 characters.' : 'La contraseña es muy débil. Usá al menos 8 caracteres.';
    if (c === 'same_password') return en ? 'The new password must be different from the previous one.' : 'La nueva contraseña tiene que ser distinta de la anterior.';
    if (st === 429 || c === 'over_request_rate_limit' || c === 'over_email_send_rate_limit') return en ? 'Too many attempts. Please try again in a few minutes.' : 'Demasiados intentos. Probá de nuevo en unos minutos.';
    if (c === 'email_provider_disabled' || c === 'provider_disabled') return en ? 'Email sign-in is disabled.' : 'El ingreso con email está desactivado en Supabase.';
    if (st === 401 || /api key/i.test(err && err.message || '')) return en ? 'Configuration error (API key).' : 'Error de configuración (clave de API).';
    var det = [st, c || (err && err.message)].filter(Boolean).join(' · ');
    return (en ? 'Something went wrong. Please try again.' : 'Algo salió mal. Probá de nuevo.') + (det ? ' [' + det + ']' : '');
  }

  return {
    message: message,

    signIn: async function (email, password) {
      var d = await req('/auth/v1/token?grant_type=password', {
        method: 'POST', headers: hdr(), body: JSON.stringify({ email: email, password: password })
      });
      return save(d);
    },

    /* Sesión vigente (la renueva si venció). null si no hay. */
    session: async function () {
      var s = load();
      if (!s || !s.access_token) return null;
      if (s.expires_at - 60 > now()) return s;
      try {
        var d = await req('/auth/v1/token?grant_type=refresh_token', {
          method: 'POST', headers: hdr(), body: JSON.stringify({ refresh_token: s.refresh_token })
        });
        return save(d);
      } catch (e) {
        if (e.code !== 'network') clear();
        return null;
      }
    },

    hasSession: function () { var s = load(); return !!(s && s.refresh_token); },

    recover: function (email, redirectTo) {
      return req('/auth/v1/recover?redirect_to=' + encodeURIComponent(redirectTo), {
        method: 'POST', headers: hdr(), body: JSON.stringify({ email: email })
      });
    },

    updatePassword: async function (password) {
      var s = await this.session();
      if (!s) { var e = new Error('no session'); e.code = 'no_session'; throw e; }
      var u = await req('/auth/v1/user', {
        method: 'PUT', headers: hdr(s.access_token), body: JSON.stringify({ password: password })
      });
      s.user = u; return save(s);
    },

    /* Procesa el enlace que llega por mail (recuperar contraseña / invitación).
       Devuelve {type} si había un enlace válido, {error} si venía con error, o null. */
    fromLink: async function () {
      var h = location.hash.replace(/^#/, '');
      if (!h) return null;
      var p = new URLSearchParams(h);
      var clean = function () { history.replaceState(null, '', location.pathname + location.search); };
      if (p.get('error') || p.get('error_code')) { clean(); return { error: p.get('error_code') || p.get('error') }; }
      var at = p.get('access_token');
      if (!at) return null;
      var d = {
        access_token: at,
        refresh_token: p.get('refresh_token'),
        expires_at: Number(p.get('expires_at')) || 0,
        expires_in: p.get('expires_in')
      };
      try { d.user = await req('/auth/v1/user', { headers: hdr(at) }); } catch (e) {}
      save(d); clean();
      return { type: p.get('type') || '' };
    },

    signOut: async function () {
      var s = load(); clear();
      if (s && s.access_token) {
        try { await fetch(BASE + '/auth/v1/logout', { method: 'POST', headers: hdr(s.access_token) }); } catch (e) {}
      }
    }
  };
})();
