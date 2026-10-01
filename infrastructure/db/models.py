"""Modelos ORM (Postgres): usuario local (espejo de Keycloak), informe guardado
e hexágono (espejo persistido del `.parquet`, fuente de runtime del mapa)."""
from __future__ import annotations
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    BigInteger, Boolean, DateTime, Float, ForeignKey, Integer, String, TypeDecorator,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from infrastructure.db.session import Base


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(timezone.utc)


class H3Index(TypeDecorator):
    """PK de celda H3: se ALMACENA como ``BIGINT`` (el entero nativo de 64 bits del
    índice H3) y se EXPONE a la app como el token hex en ``str`` — que es lo que
    usan h3-js, el front y toda la capa de dominio/servicios/API. Así el cambio de
    tipo vive SOLO en la BD: el contrato externo no cambia.

    El token H3 es la representación hexadecimal del entero, así que ``int(s, 16)``
    y ``format(i, "x")`` son inversos exactos (el bit alto reservado es 0 → entra
    en BIGINT con signo sin desbordar). Cualquier acceso por ORM/Core (``.in_()``,
    ``==``, ``select(col)``, ``insert().values``, ``bulk_insert_mappings``,
    ``db.get``) convierte solo; únicamente el SQL crudo (COPY, blue-green) convierte
    a mano."""

    impl = BigInteger
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, int):
            return value
        return int(str(value), 16)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return format(int(value), "x")


class AppUser(Base):
    """
    Espejo local del usuario de Keycloak (fuente de verdad = Keycloak).
    Se provisiona/actualiza en cada request autenticado (ver keycloak_guard).
    """
    __tablename__ = "app_user"

    sub: Mapped[str] = mapped_column(String, primary_key=True)  # `sub` del JWT
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    roles: Mapped[list] = mapped_column(JSONB, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Informe(Base):
    """
    Informe de prefactibilidad guardado por un usuario. Se persisten los
    PARÁMETROS compactados (no el PDF); el informe se regenera al reabrirlo.
    """
    __tablename__ = "informe"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(
        ForeignKey("app_user.sub", ondelete="CASCADE"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )
    nombre: Mapped[str] = mapped_column(String)
    hexes: Mapped[list] = mapped_column(JSONB, default=list)
    variables: Mapped[list] = mapped_column(JSONB, default=list)
    caracteristicas: Mapped[dict] = mapped_column(JSONB, default=dict)
    scores_snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    region: Mapped[str | None] = mapped_column(String, nullable=True)
    area_km2: Mapped[float] = mapped_column(Float, default=0.0)
    # Versión de la MASTER con la que se generó el informe (trazabilidad: qué
    # datos vio). Se estampa al guardar (ver historial_service).
    master_version: Mapped[str | None] = mapped_column(String, nullable=True)


class MasterVersion(Base):
    """
    Versión de la MASTER (dataset de hexágonos). Cada actualización crea una fila
    con su estado y momento de aplicación. La versión ACTIVA = la más reciente en
    estado `ready`. Los informes referencian la versión con la que se generaron.

    Estados: `ready` (aplicada y vigente) · `updating` (ingesta en curso —
    los informes sacados mientras tanto pueden salir inconsistentes) ·
    `scheduled` (programada para `scheduled_at`) · `failed`.
    """
    __tablename__ = "master_version"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    version: Mapped[str] = mapped_column(String)  # etiqueta legible (ej. "2026-09-12T18:00")
    status: Mapped[str] = mapped_column(String, default="ready")
    source: Mapped[str | None] = mapped_column(String, nullable=True)  # ruta parquet
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    hex_count: Mapped[int] = mapped_column(Integer, default=0)
    var_count: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, index=True
    )


class Hexagon(Base):
    """
    Espejo persistido de una fila del `.h3.parquet`. Es la FUENTE DE RUNTIME del
    mapa: el `warmup` carga esta tabla al índice H3 en memoria (antes leía el
    parquet directo). El parquet queda como ENTRADA DE INGESTA, no como runtime.

    Las variables del modelo (`REG_034`, `TEC_001_sv001`, `SOE_001`, …) son
    dinámicas, dispersas y heterogéneas (str | float | None); se guardan en un
    único `variables` JSONB para no acoplar el esquema al set de columnas del
    parquet (que cambia cuando llega el dataset definitivo).

    Mantenimiento: `hexagon_repository.replace_all` (ingesta DESTRUCTIVA, para las
    muestras) y las operaciones CRUD (para mantenerla al día luego).
    """
    __tablename__ = "hexagon"

    # PK = índice H3 almacenado como BIGINT (ver H3Index); la app lo ve como str hex.
    h3_index: Mapped[str] = mapped_column(H3Index, primary_key=True)
    score_general: Mapped[float] = mapped_column(Float, default=0.0)
    tipo_celda: Mapped[str] = mapped_column(String, default="disponible")
    # lat/lng se calculan del h3_index EN LA INGESTA y se persisten: permiten las
    # consultas por bbox (tiles) sin cargar nada a memoria (btree en la migración).
    lat: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    lng: Mapped[float | None] = mapped_column(Float, nullable=True, index=True)
    variables: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Scores PRECALCULADOS del modelo (bundle): por criterio/subcriterio. `global`
    # va aparte en `score_general`. JSONB para no acoplar el esquema a los criterios.
    scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Emplazamiento del bundle de origen (onshore ahora; offshore = bundle futuro).
    emplazamiento: Mapped[str] = mapped_column(String, default="onshore", index=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now
    )


class CatalogVariable(Base):
    """
    Catálogo de administración de variables (US08-01). Persiste el detalle y los
    overrides que el admin edita (habilitar, ver-en-mapa/menú, dataset). Las del
    modelo (`tipo="modelo"`) NO se deshabilitan; las informativas sí, y se pueden
    crear. `id` es el slug del catálogo (ej. "wpd", "turbulencia").
    """
    __tablename__ = "catalog_variable"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String)
    dimension: Mapped[str] = mapped_column(String)
    dimension_label: Mapped[str] = mapped_column(String, default="")
    tipo: Mapped[str] = mapped_column(String, default="informativa")  # modelo|informativa
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    en_mapa: Mapped[bool] = mapped_column(Boolean, default=False)
    en_menu: Mapped[bool] = mapped_column(Boolean, default=True)
    excluyente: Mapped[bool] = mapped_column(Boolean, default=False)
    fuente: Mapped[str] = mapped_column(String, default="Por definir")
    unidad: Mapped[str] = mapped_column(String, default="")
    rango: Mapped[str] = mapped_column(String, default="")
    descripcion: Mapped[str] = mapped_column(String, default="")
    dataset_version: Mapped[str] = mapped_column(String, default="v1.0")
    dataset_updated_at: Mapped[str] = mapped_column(String, default="")
    # ── Datos reales por-variable que ANTES estaban hardcodeados en el front
    #    (variablesCatalog.ts / variableWeights.ts / mapLayerData.ts). Se editan
    #    desde el panel de admin y persisten (a diferencia de la master, que se
    #    reingesta). El seam del front los prefiere sobre el template.
    master_id: Mapped[str] = mapped_column(String, default="NA")     # p.ej. "TEC.001"
    criterio: Mapped[str] = mapped_column(String, default="")
    subcriterio: Mapped[str] = mapped_column(String, default="")
    aplica_en: Mapped[str] = mapped_column(String, default="")        # ""|mar|tierra
    no_calculo: Mapped[bool] = mapped_column(Boolean, default=False)  # informativa: no puntúa
    polaridad: Mapped[int] = mapped_column(Integer, default=0)        # +1 suma / -1 resta / 0
    peso: Mapped[float] = mapped_column(Float, default=1.0)           # |peso| en el WLC
    map_kind: Mapped[str] = mapped_column(String, default="")         # ""|calor|interconectado|area
    map_threshold: Mapped[float] = mapped_column(Float, default=0.0)  # umbral heatmap
    # ── Metadatos de InfoVariables(2) (Fase 1 del rediseño anidado) ──
    #    Alimentados por el actualizador de metadatos conciliando por `master_id`.
    subcriterio_id: Mapped[str] = mapped_column(String, default="")   # FK a catalog_subcriterio
    emplazamiento: Mapped[str] = mapped_column(String, default="both")  # onshore|offshore|both
    licencia: Mapped[str] = mapped_column(String, default="")
    url_fuente: Mapped[str] = mapped_column(String, default="")
    icon_key: Mapped[str] = mapped_column(String, default="")
    # clase (más fina que `tipo`): modelo|informativa|alerta|excluyente|campo
    clase: Mapped[str] = mapped_column(String, default="informativa")
    # "Comentarios/ideas sobre la forma de visualización" (InfoVariables informativas).
    comentarios: Mapped[str] = mapped_column(String, default="")
    # "Cómo se calcula" la variable (método/fórmula, texto libre). Editable en admin
    # e ingestable por metadatos; el front lo prefiere sobre el texto template.
    metodo_calculo: Mapped[str] = mapped_column(String, default="")


class Norma(Base):
    """Norma regulatoria del catálogo (US08-02). Alimenta el semáforo del informe."""
    __tablename__ = "catalog_norma"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    nombre: Mapped[str] = mapped_column(String)
    descripcion: Mapped[str] = mapped_column(String, default="")
    # aplica | tramite | verificacion | no-aplica
    estado: Mapped[str] = mapped_column(String, default="verificacion")
    # ── Metadatos de InfoVariables regulatoria (antes se perdían) ──
    fuente: Mapped[str] = mapped_column(String, default="")
    url_fuente: Mapped[str] = mapped_column(String, default="")
    condicion: Mapped[str] = mapped_column(String, default="")   # condición de activación
    # Código de variable a la que aplica (Regulación-Social/Técnica/Ambiental);
    # "" en normas generales (REG-…).
    variable_ref: Mapped[str] = mapped_column(String, default="", index=True)


class Escenario(Base):
    """Escenario económico del catálogo (US08-03). Alimenta el panel económico."""
    __tablename__ = "catalog_escenario"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=_uuid)
    label: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")
    horizonte: Mapped[int] = mapped_column(Integer, default=10)  # años
    # ── Económico real → BD (antes hardcode en economicScenarios.ts) ──
    # `mult`: factor que modela la curva de la gráfica económica por escenario.
    # `impacto`: proyección ILUSTRATIVA (no definitiva) de cómo, considerando
    # todas las variables, el proyecto se podría sostener. Editable en admin.
    mult: Mapped[float] = mapped_column(Float, default=1.03)
    impacto: Mapped[str] = mapped_column(String, default="")


class Turbine(Base):
    """
    Especificación de aerogenerador por categoría (S/M/L) — item 4. Antes
    hardcodeado en `projectData.ts` (`AEROGENERADORES`). Real → BD, editable en
    admin: potencia nominal, área y espaciamiento por turbina alimentan el cálculo
    de nº de turbinas, rango de área por capacidad y el factor de la proyección.
    """
    __tablename__ = "catalog_turbine"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # S | M | L
    label: Mapped[str] = mapped_column(String)
    detail: Mapped[str] = mapped_column(String, default="")
    # Potencia por aerogenerador — RANGO REAL (Tabla 2 del cliente), en MW.
    # `potencia_max_mw == 0` = sin tope superior (utility "1 MW o más").
    potencia_min_mw: Mapped[float] = mapped_column(Float, default=0.0)
    potencia_max_mw: Mapped[float] = mapped_column(Float, default=0.0)
    # ⚠️ TEMPLATE: el cliente NO envió área/espaciamiento por turbina.
    area_por_aero_km2: Mapped[float] = mapped_column(Float, default=0.0)
    espaciamiento_m: Mapped[int] = mapped_column(Integer, default=0)


class DataSource(Base):
    """
    Registro de FUENTES de datos ingeridas: qué doc, DÓNDE está guardado y el
    estado de su última ingesta. Una fila por doc (master, variables, normas,
    escenarios, turbinas). Así queda persistido dónde vive cada fuente (p.ej. la
    ruta del parquet de la master) y cuándo/con qué resultado se ingirió.
    """
    __tablename__ = "data_source"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # master|variables|normas|...
    label: Mapped[str] = mapped_column(String, default="")
    kind: Mapped[str] = mapped_column(String, default="csv")   # parquet|csv|json
    location: Mapped[str] = mapped_column(String, default="")  # ruta/URL donde se guarda
    rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")  # ok|error|pending
    ingested_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[str] = mapped_column(String, default="")


class ParquetColumn(Base):
    """
    Registro columna→clase del motor de ingesta schema-agnóstico
    (docs/spec/ingesta-clases-conciliacion.md). Solo contiene columnas que un
    humano YA confirmó; lo desconocido NO está aquí → la ingesta lo marca
    `pendiente` de conciliación (nunca adivina).
    """
    __tablename__ = "parquet_column"

    name: Mapped[str] = mapped_column(String, primary_key=True)   # "AMB_005", "global", "AMB_005_revisada"
    base_code: Mapped[str] = mapped_column(String, default="", index=True)
    # clase: clave|variable_modelo|variable_informativa|alerta|excluyente|
    #        criterio|subcriterio|global|bandera|campo|ignorar|<nueva>
    kind: Mapped[str] = mapped_column(String, default="variable_informativa")
    # entidad/campo al que se adjunta (si kind=campo/bandera); id de catálogo si variable
    target_ref: Mapped[str] = mapped_column(String, default="")
    usos: Mapped[str] = mapped_column(String, default="")          # csv: mapa,heatmap,score,informe,detalle
    tipo_dato: Mapped[str] = mapped_column(String, default="")     # double|string|int|bool
    estado: Mapped[str] = mapped_column(String, default="ok")      # ok|pendiente|ignorada
    nota: Mapped[str] = mapped_column(String, default="")


class HexagonMeta(Base):
    """
    Metadatos AGREGADOS del dataset, precalculados en la ingesta (una pasada):
    rango [min, max] por variable para normalizar, y el catálogo de variables.

    Evita el barrido de todo el dataset en cada request (lo que antes hacía el
    índice en memoria). Es una tabla diminuta: una fila por variable.
    """
    __tablename__ = "hexagon_meta"

    variable: Mapped[str] = mapped_column(String, primary_key=True)
    vmin: Mapped[float] = mapped_column(Float)
    vmax: Mapped[float] = mapped_column(Float)


class HexagonVariablesPon(Base):
    """
    Variables PONDERADAS por hex (`variables_pon.h3.parquet`), en tabla PROPIA
    (Fase 2). Se guardan aparte de las crudas (`hexagon.variables`) porque el
    "descargar crudos" del informe las necesita — decisión winx: no se desperdician
    datos. Un JSONB {code: valor_ponderado}.

    Separación onshore/offshore por TABLAS SEPARADAS: esta es ONSHORE; el offshore
    será `hexagon_variables_pon_offshore` (tabla nueva) cuando llegue ese bundle.
    """
    __tablename__ = "hexagon_variables_pon"

    h3_index: Mapped[str] = mapped_column(H3Index, primary_key=True)
    variables_pon: Mapped[dict] = mapped_column(JSONB, default=dict)


# ══════════════════════════════════════════════════════════════════════════════
#  Catálogo JERÁRQUICO (Fase 1 del rediseño anidado) — dimensión → criterio →
#  subcriterio → variable. Fiel al modelo del bundle (docs/spec/modelo-datos-
#  anidado.md). Editable por admin; alimentado por el actualizador de metadatos.
# ══════════════════════════════════════════════════════════════════════════════

class Dimension(Base):
    """Dimensión del modelo (Técnica, Ambiental, Social, Económica, Regulatoria)."""
    __tablename__ = "catalog_dimension"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # slug: tecnica|ambiental|...
    nombre: Mapped[str] = mapped_column(String, default="")
    definicion: Mapped[str] = mapped_column(String, default="")
    orden: Mapped[int] = mapped_column(Integer, default=0)
    puntua: Mapped[bool] = mapped_column(Boolean, default=True)  # False = informativa (Econ/Regul)


class Criterio(Base):
    """Criterio del modelo (recurso_eolico, infraestructura, contexto_biotico…)."""
    __tablename__ = "catalog_criterio"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    dimension_id: Mapped[str] = mapped_column(String, default="", index=True)
    code: Mapped[str] = mapped_column(String, default="")  # columna del bundle (recurso_eolico…)
    nombre: Mapped[str] = mapped_column(String, default="")
    definicion: Mapped[str] = mapped_column(String, default="")
    peso: Mapped[float] = mapped_column(Float, default=0.0)


class Subcriterio(Base):
    """Subcriterio del modelo (densidad_wpd, dist_nodos, vulnerabilidad…)."""
    __tablename__ = "catalog_subcriterio"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    criterio_id: Mapped[str] = mapped_column(String, default="", index=True)
    code: Mapped[str] = mapped_column(String, default="")  # columna del bundle (densidad_wpd…)
    nombre: Mapped[str] = mapped_column(String, default="")
    definicion: Mapped[str] = mapped_column(String, default="")


class EconomicPanel(Base):
    """Catálogo de hojas del bundle económico (de la hoja RESUMEN). `curado` = se
    muestra en el panel económico de la app (Fase 3)."""
    __tablename__ = "economic_panel"

    id: Mapped[str] = mapped_column(String, primary_key=True)  # nombre de hoja
    nombre: Mapped[str] = mapped_column(String, default="")
    descripcion: Mapped[str] = mapped_column(String, default="")
    curado: Mapped[bool] = mapped_column(Boolean, default=False)


class EconomicSeries(Base):
    """
    Serie económica LONG (Fase 3): una fila por punto de las 26 hojas del bundle
    macro. `panel` = hoja de origen; `cat1..cat3` = categorías del parámetro;
    `escenario_mw` = escenario de MW de generación; `anio` + `valor`. Informativa,
    no toca `hexagon`. Ver docs/spec/modelo-datos-anidado.md §6.3.
    """
    __tablename__ = "economic_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    panel: Mapped[str] = mapped_column(String, index=True)
    escenario_mw: Mapped[int] = mapped_column(Integer, default=0)
    cat1: Mapped[str] = mapped_column(String, default="")
    cat2: Mapped[str] = mapped_column(String, default="")
    cat3: Mapped[str] = mapped_column(String, default="")
    anio: Mapped[int] = mapped_column(Integer, default=0)
    valor: Mapped[float] = mapped_column(Float, default=0.0)


class ColumnMapping(Base):
    """
    Perfil de importación: mapea el ENCABEZADO de un CSV/XLSX de metadatos al
    CAMPO destino del catálogo. Lo usa `metadata_ingest` para clasificar columnas
    (conocida → destino; desconocida → pendiente de conciliación). Un perfil por
    tipo de doc (`infovariables`, `economico`, …).
    """
    __tablename__ = "column_mapping"

    id: Mapped[str] = mapped_column(String, primary_key=True)   # "<perfil>:<encabezado>"
    perfil: Mapped[str] = mapped_column(String, default="", index=True)
    encabezado: Mapped[str] = mapped_column(String, default="")
    destino: Mapped[str] = mapped_column(String, default="")    # campo del catálogo
    transform: Mapped[str] = mapped_column(String, default="")  # emplazamiento|bool_si_no|texto
    estado: Mapped[str] = mapped_column(String, default="ok")   # ok|pendiente|ignorada
    nota: Mapped[str] = mapped_column(String, default="")


class AppConfig(Base):
    """
    Config editable clave→valor (JSONB). Store genérico para textos/parámetros
    que el admin ajusta sin tocar código (p.ej. `report_calc_text` = el "Resumen
    del cálculo" del informe). El valor es un blob JSON libre; cada consumidor
    conoce su forma. Reutilizable para otros ajustes (intervalos, etc.).
    """
    __tablename__ = "app_config"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONB, default=dict)
