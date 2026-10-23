import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

from .modelo import MicroResultado
from .motor import cargar_criterios, evaluar_micro
from .reportes import (generar_csv, generar_excel, generar_guia_md, generar_json, generar_md,
                       meta_ejecucion)
from .scan import Proyecto

RAIZ = Path(__file__).resolve().parent.parent


def _git(ruta, *args):
    try:
        return subprocess.run(["git", "-C", str(ruta), *args], capture_output=True, check=True,
                              text=True).stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError, OSError):
        return ""


def _clonar(repo, rama, destino):
    token = os.environ.get("GITLAB_READ_TOKEN")  # repos privados/internos: token de solo lectura (variable CI enmascarada)
    if token and repo.startswith("https://"):
        repo = repo.replace("https://", f"https://oauth2:{token}@", 1)
    cmd = ["git", "clone", "-q", "--depth", "1"] + (["--branch", rama] if rama else []) + [repo, str(destino)]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def _prs_de(entrada, defecto):
    """PRS explicito en el yml, o deducido de la URL del repo (as241s5_prsN), o el del archivo."""
    if entrada.get("prs"):
        return str(entrada["prs"])
    m = re.search(r"as\d+s\d+_(prs\d+)", entrada.get("repo", ""), re.I)
    return m.group(1).upper() if m else defecto


def _micros_de_standards(ruta, workdir):
    data = yaml.safe_load(Path(ruta).read_text(encoding="utf-8"))
    base = Path(ruta).resolve().parent
    micros = []
    for m in data["micros"]:
        entrada = {"nombre": m["nombre"], "rama": m.get("rama", ""), "ruta": None, "error": "",
                   "prs": _prs_de(m, data.get("prs", "PRS"))}
        try:
            if m.get("repo"):
                destino = Path(workdir) / m["nombre"]
                _clonar(m["repo"], m.get("rama"), destino)
                entrada["ruta"] = destino
            else:
                entrada["ruta"] = (base / m["ruta"]).resolve()
                if not entrada["ruta"].is_dir():
                    raise FileNotFoundError(f"No existe la ruta {m['ruta']}")
        except subprocess.CalledProcessError as e:
            # No incluir el comando: la URL podria llevar credenciales.
            entrada["error"] = f"No se pudo clonar el repositorio (git termino con codigo {e.returncode})"
        except Exception as e:  # noqa: BLE001
            entrada["error"] = f"No se pudo obtener el micro: {type(e).__name__}: {str(e)[:150]}"
        micros.append(entrada)
    return data.get("prs", "PRS"), micros


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="Verifica el estandar de arquitectura en micros Java.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--micro", help="Ruta de UN micro (modo pipeline por micro)")
    g.add_argument("--standards", help="standards.yml con la lista de micros de un PRS (modo consolidado)")
    ap.add_argument("--nombre", help="Nombre del micro (con --micro)")
    ap.add_argument("--rama", default="", help="Rama (con --micro; si falta se lee de git)")
    ap.add_argument("--commit", default="", help="Commit (con --micro; si falta se lee de git)")
    ap.add_argument("--prs", default="", help="Nombre del PRS para el reporte")
    ap.add_argument("--criterios", default=str(RAIZ / "criterios.yml"))
    ap.add_argument("--out", default="salida", help="Carpeta de salida")
    ap.add_argument("--publicar", default="",
                    help="Destino del sheet: local:CARPETA (simulado), google:ID (cuenta de servicio) o webhook:URL "
                         "(Apps Script; clave en la variable STANDARDS_WEBHOOK_SECRET)")
    ap.add_argument("--fail-under", type=float, default=None,
                    help="Si el score de algun micro es menor, termina con codigo 1 (despues de generar reportes)")
    a = ap.parse_args(argv)

    version, estandar, criterios = cargar_criterios(a.criterios)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    if a.micro:
        ruta = Path(a.micro).resolve()
        prs = a.prs or _prs_de({"repo": os.environ.get("CI_PROJECT_NAMESPACE", "")}, "PRS")
        # En GitLab CI la ruta del proyecto manda: es lo que el sheet valida y no depende de como se escribio --nombre.
        ruta_ci = os.environ.get("CI_PROJECT_PATH", "")
        nombre = ruta_ci.rsplit("/", 1)[-1] if ruta_ci else (a.nombre or ruta.name)
        rama = a.rama or os.environ.get("CI_COMMIT_REF_NAME", "") or _git(ruta, "rev-parse", "--abbrev-ref", "HEAD")
        entradas = [{"nombre": nombre, "rama": rama, "ruta": ruta, "error": "", "prs": prs}]
        workdir = None
    else:
        workdir = tempfile.TemporaryDirectory(prefix="standards-checker-")
        prs, entradas = _micros_de_standards(a.standards, workdir.name)
        prs = a.prs or prs

    resultados = []
    for e in entradas:
        m = MicroResultado(nombre=e["nombre"], prs=e["prs"], rama=e["rama"], error=e["error"])
        if not e["error"]:
            m.ruta = str(e["ruta"])
            m.commit = ((a.commit or os.environ.get("CI_COMMIT_SHORT_SHA", "")) if a.micro else "")                 or _git(e["ruta"], "rev-parse", "--short", "HEAD")
            m.resultados = evaluar_micro(Proyecto(e["ruta"], e["nombre"], m.rama, m.commit), criterios)
        resultados.append(m)

    meta = meta_ejecucion(version, estandar)
    generar_excel(prs, resultados, out / "reporte-estandares.xlsx", meta, criterios)
    generar_md(prs, resultados, out / "reporte-estandares.md", meta)
    generar_csv(prs, resultados, out / "reporte-estandares.csv")
    generar_json(prs, resultados, out / "resultados.json", meta)
    generar_guia_md(criterios, out / "guia-correcciones.md", meta)
    if workdir:
        workdir.cleanup()

    if a.publicar:
        from .publicar import armar_envio, crear_escritor, enviar_webhook, publicar
        datos = json.loads((out / "resultados.json").read_text(encoding="utf-8"))
        if a.publicar.startswith("webhook:"):
            # Nunca debe romper el pipeline del micro: si falla, se avisa (sin imprimir la URL) y se sigue.
            try:
                resp = enviar_webhook(a.publicar[len("webhook:"):], os.environ.get("STANDARDS_WEBHOOK_SECRET", ""),
                                      armar_envio(datos, criterios))
                print(f"Reporte enviado al sheet: {resp}")
            except Exception as e:  # noqa: BLE001
                print(f"AVISO: no se pudo enviar el reporte al sheet ({type(e).__name__}); el pipeline continua")
        else:
            resumen = publicar(datos, criterios, crear_escritor(a.publicar))
            print(f"Publicado en {a.publicar.split(':', 1)[0]}: {resumen}")
    print(f"Reportes en: {out.resolve()}")
    peor = None
    for m in resultados:
        t = m.score()
        print(f"  {m.nombre:<28} score={t}"
              + (f"  [{m.error}]" if m.error else ""))
        if t is not None:
            peor = t if peor is None else min(peor, t)
    if a.fail_under is not None and peor is not None and peor < a.fail_under:
        print(f"Score minimo {peor} < umbral {a.fail_under}: termina con codigo 1")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
