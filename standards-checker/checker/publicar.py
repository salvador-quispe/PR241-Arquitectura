"""Publica los resultados en un 'sheet' (proceso central).

Pestanas que escribe el codigo (todo lo demas, como 'Mi micro', lo arma el sheet con formulas):
  Resumen   una fila por micro (PRS + micro + rama); se reemplaza si ya existe, se agrega si es nuevo
  Detalle   una fila por micro y criterio; se reemplaza el bloque del micro, la Observacion va al final
  Historial solo agrega: una fila por micro y ejecucion (no repite si commit y score no cambiaron)
  Criterios la guia de correccion (se reescribe completa)

Destinos: 'local:CARPETA' (sheet simulado: un CSV por pestana) y 'google:ID' (Google Sheets, credenciales de la
cuenta de servicio en la variable de entorno GOOGLE_SERVICE_ACCOUNT_JSON; requiere 'pip install gspread').
"""
import base64
import csv
import json
import os
from datetime import datetime
from pathlib import Path

from .modelo import ORDEN_SEVERIDAD

RESUMEN = ["PRS", "Micro", "Rama", "Commit", "Score (%)", "Cumple", "Parcial", "No cumple", "Revision manual",
           "Actualizado"]
DETALLE = ["PRS", "Micro", "Rama", "Criterio ID", "Seccion", "Criterio", "Resultado", "%", "Severidad", "Donde",
           "Que cambiar", "Observacion"]
HISTORIAL = ["Fecha", "PRS", "Micro", "Rama", "Commit", "Score (%)", "Cumple", "Parcial", "No cumple",
             "Revision manual"]
CRITERIOS = ["ID", "Seccion", "Criterio", "Severidad", "Peso", "Que cambiar", "Donde debe quedar", "Opciones",
             "Ejemplo"]


# ---------------------------------------------------------------- escritores
class EscritorLocal:
    """Sheet simulado: un CSV por pestana dentro de una carpeta."""

    def __init__(self, carpeta):
        self.carpeta = Path(carpeta)
        self.carpeta.mkdir(parents=True, exist_ok=True)

    def _ruta(self, hoja):
        return self.carpeta / f"{hoja}.csv"

    def leer(self, hoja):
        r = self._ruta(hoja)
        if not r.exists():
            return []
        with open(r, newline="", encoding="utf-8-sig") as fh:
            return [fila for fila in csv.reader(fh)]

    def escribir(self, hoja, filas):
        with open(self._ruta(hoja), "w", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(filas)

    def agregar(self, hoja, filas):
        with open(self._ruta(hoja), "a", newline="", encoding="utf-8-sig") as fh:
            csv.writer(fh).writerows(filas)


class EscritorGoogle:
    """Google Sheets via gspread. NO probado contra un sheet real todavia (requiere la cuenta de servicio)."""

    def __init__(self, spreadsheet_id, credenciales_json):
        import gspread  # import tardio: dependencia opcional
        cliente = gspread.service_account_from_dict(json.loads(credenciales_json))
        self.libro = cliente.open_by_key(spreadsheet_id)

    def _hoja(self, nombre):
        try:
            return self.libro.worksheet(nombre)
        except Exception:  # noqa: BLE001 - la pestana no existe todavia
            return self.libro.add_worksheet(title=nombre, rows=1000, cols=20)

    def leer(self, hoja):
        return self._hoja(hoja).get_all_values()

    def escribir(self, hoja, filas):
        ws = self._hoja(hoja)
        ws.clear()
        if filas:
            ws.update(values=filas, range_name="A1", value_input_option="USER_ENTERED")

    def agregar(self, hoja, filas):
        if filas:
            self._hoja(hoja).append_rows(filas, value_input_option="USER_ENTERED")


def credenciales_google(env=None):
    """Clave de la cuenta de servicio: JSON en GOOGLE_SERVICE_ACCOUNT_JSON o en base64 en GOOGLE_SERVICE_ACCOUNT_B64
    (GitLab no permite enmascarar un JSON con llaves y comillas; el base64 si)."""
    env = os.environ if env is None else env
    if env.get("GOOGLE_SERVICE_ACCOUNT_JSON"):
        return env["GOOGLE_SERVICE_ACCOUNT_JSON"]
    if env.get("GOOGLE_SERVICE_ACCOUNT_B64"):
        return base64.b64decode(env["GOOGLE_SERVICE_ACCOUNT_B64"]).decode("utf-8")
    return None


def crear_escritor(destino):
    tipo, _, valor = destino.partition(":")
    if tipo == "local":
        return EscritorLocal(valor)
    if tipo == "google":
        credenciales = credenciales_google()
        if not credenciales:
            raise SystemExit("Falta GOOGLE_SERVICE_ACCOUNT_JSON (o GOOGLE_SERVICE_ACCOUNT_B64) con la clave de la cuenta de servicio")
        return EscritorGoogle(valor, credenciales)
    raise SystemExit(f"Destino desconocido: {destino!r} (use local:CARPETA o google:ID)")


# ---------------------------------------------------------------- logica
def _num(x):
    """Texto -> numero cuando corresponde (los CSV y Sheets devuelven todo como texto)."""
    try:
        f = float(x)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return x


def _donde(c, r):
    """Solo los hallazgos llevan 'Donde'; si no hay archivos concretos, se indica donde crear lo que falta."""
    if r["estado"] not in ("NO CUMPLE", "PARCIAL"):
        return ""
    return r["donde"] or f"Crear/ajustar en: {c['donde_esperado']}"


def _clave(fila):
    return (str(fila[0]), str(fila[1]), str(fila[2]))


def _sin_encabezado(filas):
    return filas[1:] if filas else []


def _hallazgos_para_ia(m, por_id):
    """Lo unico que se le da a la IA: criterios incumplidos con su ubicacion; nunca codigo fuente."""
    hs = []
    for r in m["resultados"]:
        if r["estado"] in ("NO CUMPLE", "PARCIAL"):
            c = por_id[r["id"]]
            hs.append({"id": r["id"], "severidad": c["severidad"], "peso": c["peso"], "estado": r["estado"],
                       "criterio": c["descripcion"], "donde": _donde(c, r)[:300], "que_cambiar": c["que_cambiar"],
                       "obs": r["obs"][:200]})
    hs.sort(key=lambda h: (ORDEN_SEVERIDAD[h["severidad"]], -h["peso"], h["id"]))
    return hs


def armar_envio(datos, criterios, ahora=None):
    """Convierte resultados.json en filas listas para cualquier destino (sheet, webhook)."""
    ahora = ahora or datetime.now().strftime("%Y-%m-%d %H:%M")
    por_id = {c["id"]: c for c in criterios}
    orden_id = {c["id"]: i for i, c in enumerate(criterios)}
    micros, omitidos = [], []
    for m in datos["micros"]:
        prs = m.get("prs") or datos.get("prs", "PRS")
        if m.get("error"):
            omitidos.append(f"{m['nombre']}: {m['error']}")
            continue
        conteo = {"CUMPLE": 0, "PARCIAL": 0, "NO CUMPLE": 0, "REQUIERE REVISION MANUAL": 0}
        detalle = []
        for r in m["resultados"]:
            conteo[r["estado"]] = conteo.get(r["estado"], 0) + 1
            c = por_id[r["id"]]
            detalle.append([prs, m["nombre"], m["rama"], r["id"], c["seccion"], c["descripcion"], r["estado"],
                            r["porcentaje"] if r["porcentaje"] is not None else "", c["severidad"],
                            _donde(c, r), c["que_cambiar"], r["obs"]])
        detalle.sort(key=lambda f: orden_id.get(f[3], 999))
        score = m["score"] if m["score"] is not None else ""
        cuentas = [conteo["CUMPLE"], conteo["PARCIAL"], conteo["NO CUMPLE"], conteo["REQUIERE REVISION MANUAL"]]
        micros.append({
            "clave": [prs, m["nombre"], m["rama"]], "commit": m["commit"], "score": score,
            "resumen": [prs, m["nombre"], m["rama"], m["commit"], score, *cuentas, ahora],
            "historial": [ahora, prs, m["nombre"], m["rama"], m["commit"], score, *cuentas],
            "detalle": detalle,
            "ia": {"prs": prs, "micro": m["nombre"], "rama": m["rama"], "commit": m["commit"], "score": score,
                   "conteo": conteo, "hallazgos": _hallazgos_para_ia(m, por_id)},
        })
    filas_criterios = [
        [c["id"], c["seccion"], c["descripcion"], c["severidad"], c["peso"], c["que_cambiar"], c["donde_esperado"],
         "\n".join(c["opciones"]), c["ejemplo"]]
        for c in sorted(criterios, key=lambda c: (ORDEN_SEVERIDAD[c["severidad"]], orden_id[c["id"]]))]
    return {"version": 1, "fecha": ahora, "micros": micros, "omitidos": omitidos, "criterios": filas_criterios}


def enviar_webhook(url, secreto, envio, timeout=60):
    """POST del envio a la URL de recepcion (Apps Script). Apps Script responde con un 302 a la URL del resultado;
    urllib lo sigue como GET, que es lo que espera. Devuelve el JSON de respuesta."""
    import urllib.request
    extra = {"job_token": os.environ["CI_JOB_TOKEN"]} if os.environ.get("CI_JOB_TOKEN") else {}
    if secreto:
        extra["secret"] = secreto
    cuerpo = json.dumps({**extra, **envio}, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=cuerpo, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - la URL la define el comite
        return json.loads(r.read().decode("utf-8"))


def publicar(datos, criterios, escritor, ahora=None):
    """Aplica el envio sobre un escritor (sheet simulado o Google). Devuelve un resumen de lo que hizo."""
    envio = armar_envio(datos, criterios, ahora)
    nuevos, actualizados = [], []

    resumen = {_clave(f): f for f in _sin_encabezado(escritor.leer("Resumen"))}
    detalle = _sin_encabezado(escritor.leer("Detalle"))
    historial = _sin_encabezado(escritor.leer("Historial"))
    ultimo_hist = {(f[1], f[2], f[3]): f for f in historial}
    orden_id = {c["id"]: i for i, c in enumerate(criterios)}

    publicados, filas_detalle, filas_hist = set(), [], []
    for mm in envio["micros"]:
        clave = tuple(mm["clave"])
        (actualizados if clave in resumen else nuevos).append(clave[1])
        resumen[clave] = mm["resumen"]
        publicados.add(clave)
        filas_detalle += mm["detalle"]
        previa = ultimo_hist.get(clave)
        h = mm["historial"]
        if not previa or (str(previa[4]) != str(h[4]) or _num(previa[5]) != _num(h[5])):
            filas_hist.append(h)

    conservadas = [f for f in detalle if _clave(f) not in publicados]
    todas = conservadas + filas_detalle
    todas.sort(key=lambda f: (f[0], f[1], f[2], orden_id.get(f[3], 999)))
    escritor.escribir("Detalle", [DETALLE] + todas)

    filas_resumen = [[*f[:4], _num(f[4]), *[_num(x) for x in f[5:9]], f[9]] for f in resumen.values()]
    filas_resumen.sort(key=lambda f: (f[0], f[1], f[2]))
    escritor.escribir("Resumen", [RESUMEN] + filas_resumen)

    if not historial:
        escritor.escribir("Historial", [HISTORIAL])
    escritor.agregar("Historial", filas_hist)
    escritor.escribir("Criterios", [CRITERIOS] + envio["criterios"])

    return {"nuevos": nuevos, "actualizados": actualizados, "omitidos": envio["omitidos"],
            "filas_historial_agregadas": len(filas_hist)}
