"""Archivos y cola de Agentes de Trading: una carpeta en local, el bucket en la nube.

Toda escritura admite una condicion (`si_version`), y de eso dependen la cola y el
candado: dos procesos que compiten solo pueden ganar uno. `si_version=0` significa
"solo si no existe".
"""
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

LIMA = ZoneInfo("America/Lima")
# La vista previa tiene sus propios datos, pero comparte cola y sesion de ChatGPT.
AMBIENTE = os.getenv("AGENTES_AMBIENTE", "prod")
LEASE_MIN = 5
ID_RE = re.compile(r"^[a-z0-9-]{1,80}$")


class Conflicto(Exception):
    """Otro proceso cambio el archivo primero."""


def hoy() -> str:
    return datetime.now(LIMA).strftime("%Y-%m-%d")


def ahora() -> str:
    return datetime.now(LIMA).isoformat(timespec="seconds")


class Local:
    def __init__(self, raiz):
        self.raiz = Path(raiz)

    def _ruta(self, clave):
        ruta = (self.raiz / clave).resolve()
        if not ruta.is_relative_to(self.raiz.resolve()):
            raise ValueError("clave fuera del almacen")
        return ruta

    def leer(self, clave):
        ruta = self._ruta(clave)
        try:
            datos = ruta.read_bytes()
            return datos, ruta.stat().st_mtime_ns
        except FileNotFoundError:
            return None

    def escribir(self, clave, datos: bytes, si_version=None):
        ruta = self._ruta(clave)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        temp = ruta.with_name(f"{ruta.name}.{uuid.uuid4().hex}.tmp")
        temp.write_bytes(datos)
        cerrojo = ruta.with_name(ruta.name + ".cerrojo")
        try:
            # O_EXCL es atomico: la comparacion y el reemplazo van bajo el mismo cerrojo.
            fd = self._cerrar(cerrojo)
            try:
                if si_version is not None:
                    actual = ruta.stat().st_mtime_ns if ruta.exists() else 0
                    if actual != si_version:
                        raise Conflicto(clave)
                os.replace(temp, ruta)
                return ruta.stat().st_mtime_ns
            finally:
                os.close(fd)
                cerrojo.unlink(missing_ok=True)
        finally:
            temp.unlink(missing_ok=True)

    @staticmethod
    def _cerrar(cerrojo):
        import time
        limite = time.monotonic() + 10
        while True:
            try:
                return os.open(cerrojo, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                if time.monotonic() > limite:
                    raise Conflicto(str(cerrojo)) from None
                time.sleep(0.01)

    def listar(self, prefijo):
        # Prefijo de texto, igual que en el bucket: "estado/run-" no es una carpeta.
        raiz = self.raiz.resolve()
        if not raiz.exists():
            return []
        todos = (p.relative_to(raiz).as_posix() for p in raiz.rglob("*")
                 if p.is_file() and not p.name.endswith((".tmp", ".cerrojo")))
        return sorted(c for c in todos if c.startswith(prefijo))

    def borrar(self, clave, si_version=None):
        ruta = self._ruta(clave)
        if si_version is not None and (not ruta.exists() or ruta.stat().st_mtime_ns != si_version):
            raise Conflicto(clave)
        ruta.unlink(missing_ok=True)


class Bucket:
    def __init__(self, nombre):
        from google.cloud import storage
        self.bucket = storage.Client().bucket(nombre)

    def leer(self, clave):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        for _ in range(5):
            blob = self.bucket.get_blob(clave)
            if blob is None:
                return None
            try:
                return blob.download_as_bytes(if_generation_match=blob.generation), blob.generation
            except NotFound:
                return None
            except PreconditionFailed:   # cambio entre las dos llamadas: se lee otra vez
                continue
        raise Conflicto(clave)

    def escribir(self, clave, datos: bytes, si_version=None):
        from google.api_core.exceptions import PreconditionFailed
        blob = self.bucket.blob(clave)
        try:
            blob.upload_from_string(datos, if_generation_match=si_version)
        except PreconditionFailed:
            raise Conflicto(clave) from None
        return blob.generation

    def listar(self, prefijo):
        return sorted(b.name for b in self.bucket.list_blobs(prefix=prefijo))

    def borrar(self, clave, si_version=None):
        from google.api_core.exceptions import NotFound, PreconditionFailed
        try:
            self.bucket.blob(clave).delete(if_generation_match=si_version)
        except NotFound:
            pass
        except PreconditionFailed:
            raise Conflicto(clave) from None


def abrir():
    nombre = os.getenv("AGENTES_BUCKET")
    if nombre:
        return Bucket(nombre)
    if os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"):
        raise RuntimeError("En la nube AGENTES_BUCKET es obligatorio.")
    return Local(os.getenv("AGENTES_DATOS", Path.home() / ".agentes-de-trading"))


def prefijo(ambiente=None) -> str:
    """Los datos propios de la vista previa viven aparte; los de produccion, en la raiz."""
    return "preview/" if (ambiente or AMBIENTE) == "preview" else ""


def leer_json(almacen, clave, defecto=None):
    hallado = almacen.leer(clave)
    return defecto if hallado is None else json.loads(hallado[0])


def guardar_json(almacen, clave, valor, si_version=None):
    return almacen.escribir(clave, json.dumps(valor, ensure_ascii=False, indent=1).encode(), si_version)


# --- cola, avance y candado: compartidos por produccion y vista previa -------------------

COLA, CANDADO, SESION = "estado/cola.json", "estado/candado.json", "private/tradingagents/chatgpt-auth.json"


def _ruta_run(id_):
    return f"estado/run-{id_}.json"


def _cambiar_cola(almacen, cambio):
    """Aplica `cambio` a la lista de ids sin pisar otro cambio simultaneo."""
    for _ in range(12):
        hallado = almacen.leer(COLA)
        ids = json.loads(hallado[0]) if hallado else []
        nuevos = cambio(list(ids))
        if nuevos == ids:
            return ids
        try:
            guardar_json(almacen, COLA, nuevos, si_version=hallado[1] if hallado else 0)
            return nuevos
        except Conflicto:
            continue
    raise Conflicto(COLA)


def encolar(almacen, datos: dict, id_fijo=None) -> dict:
    """Agrega un trabajo. Con `id_fijo`, repetirlo no lo duplica (corrida diaria)."""
    marca = datetime.now(LIMA).strftime("%Y%m%d-%H%M%S")
    id_ = id_fijo or f"{marca}-{uuid.uuid4().hex[:8]}"
    if not ID_RE.match(id_):
        raise ValueError("id de trabajo invalido")
    valor = {**datos, "id": id_, "ambiente": datos.get("ambiente", AMBIENTE), "estado": "en_cola",
             "creado": ahora(), "etapa": "En cola", "avance": 0}
    try:
        guardar_json(almacen, _ruta_run(id_), valor, si_version=0)
    except Conflicto:
        # Una caida entre las dos escrituras no debe dejarlo varado: repetirlo lo completa.
        valor = trabajo(almacen, id_)
        if valor["estado"] != "en_cola":
            raise
    asegurar_en_cola(almacen, id_)
    return valor


def asegurar_en_cola(almacen, id_):
    _cambiar_cola(almacen, lambda ids: ids if id_ in ids else ids + [id_])


def sacar_de_cola(almacen, id_):
    _cambiar_cola(almacen, lambda ids: [i for i in ids if i != id_])


def trabajo(almacen, id_):
    return leer_json(almacen, _ruta_run(id_)) if ID_RE.match(id_ or "") else None


def en_cola(almacen, ambiente=None) -> list[dict]:
    """Los trabajos pendientes, en orden de llegada."""
    hallados = [trabajo(almacen, i) for i in leer_json(almacen, COLA, [])]
    return [t for t in hallados if t and t["estado"] == "en_cola" and ambiente in (None, t["ambiente"])]


def trabajos(almacen) -> list[dict]:
    hallados = [leer_json(almacen, c) for c in almacen.listar("estado/run-")]
    return sorted((t for t in hallados if t), key=lambda t: t["creado"], reverse=True)


def actualizar(almacen, id_, solo_si=None, **campos) -> dict | None:
    """Cambia un trabajo sin pisar otro cambio simultaneo. Con `solo_si`, solo desde esos estados."""
    for _ in range(8):
        hallado = almacen.leer(_ruta_run(id_))
        valor = json.loads(hallado[0])
        if solo_si and valor["estado"] not in solo_si:
            return None
        valor.update(campos)
        try:
            guardar_json(almacen, _ruta_run(id_), valor, si_version=hallado[1])
            return valor
        except Conflicto:
            continue
    raise Conflicto(id_)


def candado_vivo(almacen) -> bool:
    actual = leer_json(almacen, CANDADO)
    return bool(actual) and datetime.fromisoformat(actual["vence"]) > datetime.now(LIMA)


def tomar_candado(almacen, dueno: str) -> bool:
    """Un solo motor a la vez: dos a la vez invalidarian la sesion de ChatGPT."""
    vence = (datetime.now(LIMA) + timedelta(minutes=LEASE_MIN)).isoformat()
    hallado = almacen.leer(CANDADO)
    try:
        if hallado is None:
            guardar_json(almacen, CANDADO, {"dueno": dueno, "vence": vence}, si_version=0)
            return True
        actual = json.loads(hallado[0])
        if actual["dueno"] != dueno and datetime.fromisoformat(actual["vence"]) > datetime.now(LIMA):
            return False
        guardar_json(almacen, CANDADO, {"dueno": dueno, "vence": vence}, si_version=hallado[1])
        return True
    except Conflicto:
        # Un reintento de la libreria tras una respuesta perdida tambien da conflicto, aunque
        # la escritura fuera nuestra: lo que decide es quien figura como dueno.
        actual = leer_json(almacen, CANDADO)
        return bool(actual) and actual["dueno"] == dueno


def soltar_candado(almacen, dueno: str):
    hallado = almacen.leer(CANDADO)
    if hallado and json.loads(hallado[0])["dueno"] == dueno:
        try:
            almacen.borrar(CANDADO, si_version=hallado[1])
        except Conflicto:
            pass   # otro motor ya lo tomo: el candado es suyo


def lanzar_motor(ambiente=None):
    """Arranca el motor de un ambiente. Si ya hay uno vivo, no hace falta: el vacia la cola."""
    ambiente = ambiente or AMBIENTE
    if os.getenv("K_SERVICE") or os.getenv("CLOUD_RUN_JOB"):
        from google.cloud import run_v2
        region = os.getenv("AGENTES_REGION", "us-central1")
        job = "agentes-motor-preview" if ambiente == "preview" else "agentes-motor"
        run_v2.JobsClient().run_job(name=f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}/locations/{region}/jobs/{job}")
    else:
        import subprocess
        import sys
        subprocess.Popen([sys.executable, "-m", "motor.run"], start_new_session=True,
                         env={**os.environ, "AGENTES_AMBIENTE": ambiente})
