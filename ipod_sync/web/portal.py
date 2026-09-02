"""Setup portal: tiny stdlib HTTP server (no Flask — the Pi Zero has 427 MB).

Serves:
  GET  /                  status page + instructions + bookmarklet
  GET  /renew#<token>     landing for the bookmarklet; JS posts the token
  GET  /api/status        session + wifi + daemon summary
  POST /api/token         {"token": ...} → validate, save cookies.txt
  GET  /api/wifi/scan     visible networks
  POST /api/wifi          {"ssid", "password"} → join network
  *                       captive-portal redirect when in hotspot mode

The same portal runs in both modes: on the home LAN (http://<host>.local:8080)
and on the setup hotspot (http://10.42.0.1:8080, and any URL is redirected there).
"""

import json
import logging
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from ipod_sync.web import session, wifi

logger = logging.getLogger("portal")

# Hosts that iOS/macOS/Android probe to detect a captive portal.
CAPTIVE_PROBES = ("captive.apple.com", "connectivitycheck.gstatic.com", "detectportal.firefox.com",
                  "www.msftconnecttest.com", "clients3.google.com")


def portal_host() -> str:
    return f"{socket.gethostname().lower()}.local"


def bookmarklet(host: str, port: int) -> str:
    js = (
        "(()=>{const m=document.cookie.match(/media-user-token=([^;]+)/);"
        "if(!m){alert('Inicia sesión en Apple Music y vuelve a tocar el marcador');return;}"
        f"location.href='http://{host}:{port}/renew#'+encodeURIComponent(m[1]);}})()"
    )
    return "javascript:" + js


PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>iPod · ipod-sync</title>
<style>
body{font:16px -apple-system,Helvetica,sans-serif;background:#1c1c1e;color:#f2f2f7;margin:0;padding:20px;max-width:560px;margin:auto}
h1{font-size:22px;margin:8px 0 16px}h2{font-size:16px;margin:24px 0 8px;color:#8e8e93;text-transform:uppercase;letter-spacing:.05em}
.card{background:#2c2c2e;border-radius:12px;padding:14px 16px;margin:8px 0}
.ok{color:#30d158}.warn{color:#ffd60a}.bad{color:#ff453a}.dim{color:#8e8e93}
a.btn,button{display:block;width:100%;box-sizing:border-box;text-align:center;background:#fa2d48;color:#fff;border:0;border-radius:10px;padding:14px;font-size:17px;text-decoration:none;margin:10px 0}
button.sec{background:#3a3a3c}
input,select{width:100%;box-sizing:border-box;background:#1c1c1e;color:#fff;border:1px solid #3a3a3c;border-radius:8px;padding:12px;font-size:16px;margin:6px 0}
code{word-break:break-all;font-size:12px;color:#8e8e93}
</style></head><body>
<h1>iPod · ipod-sync</h1>
<div id="app">Cargando…</div>
<script>
const $=s=>document.querySelector(s);
async function api(p,o){const r=await fetch('/api/'+p,o);return r.json();}
function esc(s){return String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));}
async function render(){
  const s=await api('status');const se=s.session,w=s.wifi;
  const lvl={ok:'ok',expiring:'warn',expired:'bad',missing:'bad'}[se.level];
  const txt={ok:`Sesión válida · caduca en ${se.days_left} días`,expiring:`Caduca en ${se.days_left} días · renueva pronto`,
             expired:'Sesión caducada · renueva',missing:'Sin sesión de Apple Music'}[se.level];
  let h=`<h2>Apple Music</h2><div class="card"><div class="${lvl}">${txt}</div>
    <div class="dim">${se.storefront?`storefront ${se.storefront} · ${se.playlists} playlists`:''}
    ${se.last_error?'<br>'+esc(se.last_error.message):''}</div></div>
    <a class="btn" href="https://music.apple.com/">1 · Abrir Apple Music</a>
    <div class="dim">2 · En Safari, toca el marcador <b>iPod</b> (o el atajo). Vuelves aquí con la sesión renovada.</div>
    <details class="card" style="margin-top:12px"><summary>Crear el marcador (una vez)</summary>
    <p>Guarda cualquier página en Favoritos, edítala, ponle nombre <b>iPod</b> y sustituye la URL por:</p>
    <code id="bm">${esc(s.bookmarklet)}</code><button class="sec" onclick="navigator.clipboard.writeText($('#bm').textContent)">Copiar</button></details>`;
  h+=`<h2>Wi-Fi</h2><div class="card">${w.mode==='station'?`<span class="ok">Conectado a ${esc(w.connection)}</span> <span class="dim">${esc(w.ip)}</span>`:
      w.mode==='hotspot'?`<span class="warn">Modo configuración</span> · conecta el iPod a tu Wi-Fi:`:`<span class="bad">Sin conexión</span>`}
    <form onsubmit="return joinWifi(event)"><select id="ssid"><option>Buscando redes…</option></select>
    <input id="psk" type="password" placeholder="Contraseña"><button>Conectar</button></form><div id="wmsg"></div></div>`;
  h+=`<h2>iPod</h2><div class="card">${s.daemon.ipod?`<span class="ok">Conectado en ${esc(s.daemon.ipod)}</span>`:'<span class="dim">No conectado</span>'}
    <div class="dim">Biblioteca local: ${s.daemon.tracks} pistas · próxima descarga ${esc(s.daemon.download_time)}</div></div>`;
  $('#app').innerHTML=h; scan();
}
async function scan(){try{const n=await api('wifi/scan');$('#ssid').innerHTML=n.map(x=>`<option>${esc(x.ssid)}</option>`).join('')||'<option>Sin redes</option>';}catch(e){}}
async function joinWifi(e){e.preventDefault();$('#wmsg').textContent='Conectando… (si estás en iPod-Setup perderás esta página; vuelve a tu Wi-Fi y abre http://'+location.hostname+':'+location.port+')';
  const r=await api('wifi',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({ssid:$('#ssid').value,password:$('#psk').value})}).catch(()=>({}));
  $('#wmsg').textContent=r.ok?'Conectado a '+r.wifi.connection:(r.error||'Sin respuesta (normal en modo configuración)');return false;}
render();
</script></body></html>"""

RENEW_PAGE = """<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Renovando sesión</title>
<style>body{font:18px -apple-system,Helvetica,sans-serif;background:#1c1c1e;color:#f2f2f7;padding:40px 20px;text-align:center}
.ok{color:#30d158}.bad{color:#ff453a}a{color:#fa2d48}</style></head><body>
<p id="m">Validando sesión con Apple Music…</p><p><a href="/">Volver</a></p>
<script>
(async()=>{const t=decodeURIComponent(location.hash.slice(1));history.replaceState(null,'',location.pathname);
 if(!t){document.getElementById('m').innerHTML='<span class=bad>No llegó ningún token.</span>';return;}
 const r=await fetch('/api/token',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({token:t})}).then(r=>r.json()).catch(e=>({error:String(e)}));
 document.getElementById('m').innerHTML=r.ok?`<span class=ok>Sesión renovada.</span><br>storefront ${r.storefront} · ${r.playlists} playlists<br>Caduca el ${r.expires_at.slice(0,10)}`:`<span class=bad>${r.error}</span>`;})();
</script></body></html>"""


class PortalHandler(BaseHTTPRequestHandler):
    server_version = "ipod-sync"
    daemon_state = None  # callable returning dict, injected by serve()
    port = 8080

    def log_message(self, fmt, *args):  # route to logging instead of stderr
        logger.debug("%s " + fmt, self.address_string(), *args)

    # --- helpers ---
    def _send(self, code: int, body: bytes, ctype: str = "text/html; charset=utf-8", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > 65536:
            raise ValueError("body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _captive_redirect(self) -> bool:
        """In hotspot mode, any foreign host (incl. OS probes) is bounced to the portal."""
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        if host in CAPTIVE_PROBES or (host and host != wifi.HOTSPOT_IP and
                                      wifi.status().get("mode") == "hotspot"):
            self._send(302, b"", extra={"Location": f"http://{wifi.HOTSPOT_IP}:{self.port}/"})
            return True
        return False

    # --- routes ---
    def do_GET(self):
        path = urlparse(self.path).path
        if path.startswith("/api/"):
            return self._api_get(path)
        if self._captive_redirect():
            return
        if path == "/renew":
            return self._send(200, RENEW_PAGE.encode())
        return self._send(200, PAGE.encode())

    def _api_get(self, path: str):
        try:
            if path == "/api/status":
                host = (self.headers.get("Host") or portal_host()).split(":")[0]
                return self._json({
                    "session": session.status(),
                    "wifi": wifi.status(),
                    "daemon": self.daemon_state() if self.daemon_state else {},
                    "bookmarklet": bookmarklet(host, self.port),
                })
            if path == "/api/wifi/scan":
                return self._json(wifi.scan())
            return self._json({"error": "not found"}, 404)
        except Exception as e:
            logger.exception("GET %s failed", path)
            return self._json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            body = self._read_json()
            if path == "/api/token":
                state = session.renew(body.get("token", ""))
                return self._json({"ok": True, **state})
            if path == "/api/wifi":
                st = wifi.connect(body.get("ssid", ""), body.get("password", ""))
                if st.get("mode") == "station":
                    wifi.hotspot_stop()
                return self._json({"ok": True, "wifi": st})
            return self._json({"error": "not found"}, 404)
        except (session.SessionError, wifi.WifiError, ValueError) as e:
            return self._json({"ok": False, "error": str(e)}, 400)
        except Exception as e:
            logger.exception("POST %s failed", path)
            return self._json({"ok": False, "error": str(e)}, 500)


def serve(port: int = 8080, daemon_state=None, stop_event: threading.Event | None = None) -> None:
    """Run the portal until stop_event is set (or forever)."""
    PortalHandler.port = port
    PortalHandler.daemon_state = daemon_state
    httpd = ThreadingHTTPServer(("0.0.0.0", port), PortalHandler)
    httpd.daemon_threads = True
    logger.info(f"Portal listening on http://{portal_host()}:{port}")
    if stop_event is None:
        httpd.serve_forever()
        return
    t = threading.Thread(target=httpd.serve_forever, name="portal-http", daemon=True)
    t.start()
    stop_event.wait()
    httpd.shutdown()
