"""Local Streamlit view for the public PLACSP catalog."""

import os
import re
import sqlite3
import subprocess
from collections import Counter
from datetime import datetime
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import streamlit as st

from tenders import Limits, PlacspClient, TenderFilters, TenderService, TenderStore
from tenders.local import private_catalog_path
from tenders.placsp import HOSTS

_LIMITS = Limits(max_pages=2, timeout_seconds=12, retries=0)
_PAGE_SIZE = 25
_UNKNOWN = "Sin informar"
_AMOUNTS = {"estimated": "Valor estimado", "tax_exclusive": "Presupuesto sin impuestos", "total": "Total"}
_DOCUMENTS = {"legal": "Pliego administrativo", "technical": "Pliego técnico", "additional": "Anexo"}
_STATUSES = {"PRE": "Preanunciada", "PUB": "Publicada", "EV": "Evaluación",
             "ADJ_PAR": "Adjudicada parcialmente", "ADJ": "Adjudicada",
             "RES_PAR": "Resuelta parcialmente", "RES": "Resuelta", "ANUL": "Anulada"}
try:
    _MADRID = ZoneInfo("Europe/Madrid")
except ZoneInfoNotFoundError:
    _MADRID = None


def _loopback_only():
    return st.get_option("server.address") in {"127.0.0.1", "::1"}


def _open_catalog(directory: str) -> TenderService:
    return TenderService(TenderStore(private_catalog_path(directory)), PlacspClient(_LIMITS))


def _official_detail(url: str | None) -> str | None:
    if not url or not url.isascii() or any(ord(ch) <= 32 or ord(ch) == 127 for ch in url):
        return None
    try:
        parsed = urlsplit(url)
        if (parsed.scheme != "https" or parsed.netloc not in HOSTS
                or parsed.path != "/wps/poc" or "#" in url):
            return None
        query = parse_qs(parsed.query, strict_parsing=True, max_num_fields=2)
    except ValueError:
        return None
    ids = query.get("idEvl", [])
    if (query.get("uri") == ["deeplink:detalle_licitacion"] and len(ids) == 1
            and re.fullmatch(r"[A-Za-z0-9+/=_-]{1,256}", ids[0])):
        return url
    return None


def _date(value):
    if value is None:
        return _UNKNOWN
    if value.utcoffset() is None:
        return f"{value:%d/%m/%Y %H:%M} (zona desconocida)"
    if _MADRID is None:
        return f"{value:%d/%m/%Y %H:%M %z}"
    return value.astimezone(_MADRID).strftime("%d/%m/%Y %H:%M")


def _deadline(value):
    if value is None:
        return _UNKNOWN
    if value.day is None and value.local_time is None:
        return "Desconocida"
    if value.day is not None and value.local_time is not None and value.local_time.utcoffset() is not None:
        return _date(datetime.combine(value.day, value.local_time))
    day = value.day.strftime("%d/%m/%Y") if value.day is not None else "Fecha desconocida"
    if value.local_time is None:
        return day
    clock = value.local_time.strftime("%H:%M")
    zone = value.local_time.strftime("%z") or "zona sin informar"
    return f"{day} {clock} ({zone})"


def _amount(value, currency):
    symbol = "€" if currency == "EUR" else (currency or "moneda sin informar")
    if abs(value.adjusted()) > 100 or abs(value.as_tuple().exponent) > 100:
        return f"{str(value).replace('.', ',')} {symbol}"
    integer, _, fraction = format(value, ",f").partition(".")
    return f"{integer.replace(',', '.')},{fraction.ljust(2, '0')} {symbol}"


def _status(code):
    if code is None:
        return _UNKNOWN
    return f"{_STATUSES[code]} ({code})" if code in _STATUSES else code


def _sync_summary(service):
    successful = service.last_successful_sync()
    if successful:
        st.text(f"Última sincronización satisfactoria: {_date(successful.finished_at)}")
        if successful.has_more:
            st.info("La última sincronización se limitó a las primeras páginas. Quedan páginas en la fuente.")
    else:
        st.text("Última sincronización satisfactoria: ninguna")
    history = service.sync_history(limit=1)
    if history:
        latest = history[0]
        st.text(f"Último intento: {latest.pages} páginas guardadas · {latest.new_revisions} revisiones nuevas")
        if latest.status == "failed":
            st.warning("Actualización incompleta. Puedes consultar las páginas ya guardadas.")
            st.text(f"Error de sincronización: {latest.error or _UNKNOWN}")
        elif latest.status == "running":
            st.info("El último intento todavía no tiene un resultado registrado.")


def _render_detail(service, tender):
    item = tender.current.publication
    st.subheader("Detalle del expediente")
    st.subheader("Información general")
    for label, value in (
        ("Expediente", item.case.reference), ("Identidad PLACSP", item.identity.entry_id),
        ("Organismo", item.case.authority_name), ("Título", item.title),
        ("Descripción", item.description), ("Estado publicado", _status(item.status)),
        ("CPV", ", ".join(item.cpv)),
    ):
        st.text(f"{label}: {value or _UNKNOWN}")
    for scheme, value in item.case.authority_ids:
        st.text(f"Identificador del organismo ({scheme or _UNKNOWN}): {value}")
    if official_url := _official_detail(item.official_url):
        st.link_button("Abrir ficha oficial en PLACSP", official_url)
    elif item.official_url:
        st.caption("La URL publicada no coincide con el patrón de ficha oficial admitido.")

    st.subheader("Importes")
    if item.amounts:
        st.dataframe([{"Concepto": _AMOUNTS[a.kind], "Importe": _amount(a.value, a.currency)}
                      for a in item.amounts], hide_index=True)
    else:
        st.text("Importes: sin informar")
    st.subheader("Fechas y plazos")
    st.text(f"Publicado: {_date(item.published_at)}")
    st.text(f"Actualizado: {_date(item.updated_at)}")
    st.text(f"Plazo de ofertas: {_deadline(item.submission_deadline)}")
    st.text(f"Disponibilidad documental: {_deadline(item.document_deadline)}")

    st.subheader("Documentos referenciados, no descargados")
    st.caption("La disponibilidad y el contenido de estos documentos siguen sin validar.")
    if item.documents:
        st.dataframe([{"Tipo": _DOCUMENTS[doc.kind], "Referencia": doc.reference or _UNKNOWN}
                      for doc in item.documents], hide_index=True)
    else:
        st.caption("El expediente no incluye referencias documentales.")
    st.subheader("Historial de revisiones")
    st.dataframe([{"Actualización": _date(r.publication.updated_at), "Observada": _date(r.observed_at),
                   "Estado": _status(r.publication.status), "SHA-256": r.content_sha256}
                  for r in service.revisions(tender.tender_id)], hide_index=True)


def render_tenders():
    st.header("Licitaciones")
    st.caption("Fuente: Ministerio de Hacienda · PLACSP, perfiles alojados, excluidos los contratos menores.")
    st.caption("Piloto local. La cobertura se limita a las páginas consultadas de esta distribución.")
    if _MADRID is not None:
        st.caption("Fechas con zona horaria en Europe/Madrid. Los plazos sin zona conservan la hora publicada.")
    else:
        st.warning("Europe/Madrid no está disponible. Las fechas conservan el offset almacenado.")
    if not _loopback_only():
        st.error("Licitaciones requiere iniciar Streamlit con --server.address 127.0.0.1.")
        return
    directory = os.environ.get("INDAI_TENDERS_DIR", "").strip()
    if not directory:
        st.error("Configura INDAI_TENDERS_DIR con un directorio privado existente fuera del repositorio.")
        return
    try:
        service = _open_catalog(directory)
    except (OSError, ValueError, sqlite3.Error, subprocess.SubprocessError):
        st.error("No se pudo abrir el catálogo privado. Verifica la ruta y sus permisos de lectura y escritura.")
        return
    try:
        if st.button("Actualizar catálogo", key="tenders_refresh",
                     help="Hasta dos páginas, 12 segundos de tiempo de red por página y sin reintentos."):
            with st.spinner("Consultando la distribución oficial…"):
                service.refresh(max_pages=_LIMITS.max_pages)
        _sync_summary(service)
        counts = service.catalog_counts()
        st.text(f"Catálogo: {counts.cases} expedientes únicos · {counts.publications} publicaciones · "
                f"{counts.revisions} revisiones")
        st.caption("Se agrupa por número e identificador del organismo cuando constan ambos. "
                   "La tabla muestra una fila por publicación Atom.")
        text = st.text_input("Buscar por título, expediente u organismo", key="tenders_text")
        cpv = st.text_input("CPV exacto", key="tenders_cpv")
        status = st.text_input("Código de estado publicado", key="tenders_status")
        page = st.number_input("Página de resultados", min_value=1, max_value=100000, step=1, key="tenders_page")
        results = service.search(TenderFilters(
            text=text.strip() or None, cpv=cpv.strip() or None, status=status.strip().upper() or None,
            limit=_PAGE_SIZE, offset=(page - 1) * _PAGE_SIZE,
        ))
        if not results:
            st.info("No hay expedientes guardados que coincidan con esta consulta.")
            return
        st.dataframe([{"Expediente": row.current.publication.case.reference,
                       "Título": row.current.publication.title, "Organismo": row.current.publication.case.authority_name,
                       "Estado": _status(row.current.publication.status), "CPV": ", ".join(row.current.publication.cpv),
                       "Actualizado": _date(row.current.publication.updated_at)} for row in results], hide_index=True)
        labels = {}
        for row in results:
            item = row.current.publication
            labels[row.tender_id] = f"{item.case.reference or 'Expediente sin número'} · {item.title or 'Sin título'}"
        occurrences = Counter(labels.values())
        for row in results:
            if occurrences[labels[row.tender_id]] > 1:
                labels[row.tender_id] += f" · {row.current.publication.identity.entry_id}"
        selected = st.selectbox("Abrir expediente", list(labels), format_func=labels.__getitem__,
                                index=None, key="tenders_selection")
        if selected is not None and (tender := service.get_tender(selected)) is not None:
            _render_detail(service, tender)
    except sqlite3.Error:
        st.error("El catálogo local no está disponible en este momento.")
