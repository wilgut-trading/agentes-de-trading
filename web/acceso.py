"""La puerta: entrar con Google, y solo con el correo del dueno.

Se declara lo publico una a una, para que una ruta nueva nazca cerrada. La cookie se
llama `__session` porque Firebase Hosting borra todas las demas.
"""
import hashlib
import hmac
import ipaddress
import os
from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

COOKIE = "__session"
DIAS = 5
PUBLICAS = {"/login", "/manifest.webmanifest"}
PREFIJOS_PUBLICOS = ("/static/",)
CSP = "default-src 'self'; style-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
CSP_LOGIN = ("default-src 'self'; script-src 'self' https://www.gstatic.com; connect-src 'self' https://*.googleapis.com; "
             "frame-src https://*.firebaseapp.com https://*.web.app https://accounts.google.com; style-src 'self'; img-src 'self' data:; base-uri 'none'")
router = APIRouter()


def en_la_nube() -> bool:
    return bool(os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB") or os.getenv("AGENTES_BUCKET"))


def _correos() -> set[str]:
    return {c.strip().lower() for c in os.getenv("AGENTES_CORREOS", "").split(",") if c.strip()}


def _es_local(request: Request) -> bool:
    try:
        return ipaddress.ip_address(request.client.host).is_loopback
    except (AttributeError, ValueError):
        return False


def puerta_abierta(request: Request) -> bool:
    """Solo en el Mac, pedido a proposito y desde el propio Mac. En la nube, nunca."""
    return not en_la_nube() and os.getenv("AGENTES_SIN_LOGIN") == "1" and _es_local(request)


def _firebase():
    import firebase_admin
    from firebase_admin import auth
    if not firebase_admin._apps:
        firebase_admin.initialize_app()
    return auth


def _permitido(datos: dict) -> bool:
    return datos.get("email_verified") is True and (datos.get("email") or "").lower() in _correos()


def quien(request: Request) -> str | None:
    if puerta_abierta(request):
        return "local"
    cookie = request.cookies.get(COOKIE)
    if not cookie:
        return None
    try:
        datos = _firebase().verify_session_cookie(cookie, check_revoked=True)
    except Exception:
        return None
    return datos["email"].lower() if _permitido(datos) else None


def token_csrf(request: Request) -> str:
    """Sale de la cookie HttpOnly, que otro sitio no puede leer: no hace falta otra cookie."""
    base = request.cookies.get(COOKIE, "") or "local"
    return hashlib.sha256(b"csrf|" + base.encode()).hexdigest()


def _origen_valido(request: Request) -> bool:
    origen = request.headers.get("origin")
    if origen is None:
        return False
    permitidos = {o.strip() for o in os.getenv("AGENTES_ORIGENES", "").split(",") if o.strip()}
    if not en_la_nube():
        permitidos.add(f"{request.url.scheme}://{request.headers.get('host')}")
    return origen in permitidos


async def verificar_csrf(request: Request):
    """Corre en todas las rutas: una ruta nueva nace protegida."""
    if request.method in ("GET", "HEAD"):
        return
    if not _origen_valido(request):
        raise HTTPException(403, "Origen no permitido.")
    if request.url.path == "/login":
        return
    formulario = await request.form()
    dado = request.headers.get("x-csrf") or formulario.get("csrf") or ""
    if not hmac.compare_digest(str(dado), token_csrf(request)):
        raise HTTPException(403, "La página caducó. Recárgala e inténtalo de nuevo.")


def instalar(app):
    @app.middleware("http")
    async def guardia(request: Request, siguiente):
        ruta = request.url.path
        if ruta not in PUBLICAS and not ruta.startswith(PREFIJOS_PUBLICOS) and not quien(request):
            if request.headers.get("hx-request") or request.method != "GET":
                return Response(status_code=401, headers={"HX-Redirect": "/login"})
            return RedirectResponse("/login", status_code=303)
        respuesta = await siguiente(request)
        if not ruta.startswith(PREFIJOS_PUBLICOS):
            respuesta.headers["Cache-Control"] = "private, no-store"
        if ruta == "/static/sw.js":
            respuesta.headers["Service-Worker-Allowed"] = "/"
        respuesta.headers.setdefault("Content-Security-Policy", CSP_LOGIN if ruta == "/login" else CSP)
        respuesta.headers["X-Content-Type-Options"] = "nosniff"
        respuesta.headers["Referrer-Policy"] = "same-origin"
        return respuesta


@router.post("/login")
async def crear_sesion(request: Request):
    try:
        token = (await request.json()).get("idToken", "")
        auth = _firebase()
        datos = auth.verify_id_token(token, check_revoked=True)
        if not _permitido(datos):
            return JSONResponse({"detalle": "Esta cuenta no tiene acceso."}, status_code=403)
        cookie = auth.create_session_cookie(token, expires_in=timedelta(days=DIAS))
    except Exception:
        return JSONResponse({"detalle": "No se pudo verificar el inicio de sesión."}, status_code=401)
    r = JSONResponse({"ok": True})
    r.set_cookie(COOKIE, cookie, max_age=DIAS * 86400, httponly=True, secure=en_la_nube(), samesite="lax")
    return r


@router.post("/salir")
def salir(request: Request):
    r = RedirectResponse("/login", status_code=303)
    r.delete_cookie(COOKIE)
    try:
        if not puerta_abierta(request):
            datos = _firebase().verify_session_cookie(request.cookies.get(COOKIE, ""))
            _firebase().revoke_refresh_tokens(datos["sub"])
    except Exception:
        pass
    return r
