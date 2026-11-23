"""
CNO – Cadastro Nacional de Obras (Receita Federal), lido pela Base dos Dados no BigQuery.

Toda obra de construção civil precisa ser registrada no CNO. Daqui vêm obras PARTICULARES
recém-registradas na região (prédios, galpões, casas grandes), com endereço completo,
área em m², data de início e o CNPJ do responsável (que vira telefone/e-mail).

Precisa da chave do Google em GCP_CHAVE (segredo do GitHub). Sem a chave, não faz nada.
O site da Receita bloqueia servidores fora do Brasil; por isso usamos a cópia da Base dos Dados.
"""
import json, os, re, sys, tempfile, unicodedata
from datetime import date, timedelta

DATASET = "basedosdados.br_rf_cno"
DIAS = 180            # obras registradas/iniciadas nos últimos 6 meses
AREA_MIN = 300        # m²: abaixo disso é reforma pequena
MAX_OBRAS = 400       # teto por semana (localização e contato levam tempo)

# nomes possíveis de cada coluna (a Base dos Dados pode mudar nomes; o robô se adapta)
CANDIDATAS = {
    "id": ["id_cno", "cno", "id_obra"],
    "nome": ["nome", "nome_obra", "nome_empresarial"],
    "uf": ["sigla_uf", "uf"],
    "municipio": ["id_municipio", "codigo_municipio", "id_municipio_rf"],
    "cep": ["cep"],
    "tipo_logradouro": ["tipo_logradouro"],
    "logradouro": ["logradouro"],
    "numero": ["numero"],
    "bairro": ["bairro"],
    "complemento": ["complemento"],
    "area": ["area_total", "area", "area_construida"],
    "inicio": ["data_inicio", "data_inicio_obra"],
    "registro": ["data_registro", "data_cadastro", "data_inicio_responsabilidade"],
    "situacao": ["situacao", "id_situacao", "situacao_cadastral"],
    "responsavel": ["id_responsavel", "ni_responsavel", "cnpj_responsavel", "id_ni_responsavel"],
    "qualificacao": ["qualificacao_responsavel", "id_qualificacao_responsavel", "qualificacao"],
}


def _norm(s):
    return unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()


def _cliente():
    chave = os.environ.get("GCP_CHAVE", "").strip()
    if not chave:
        return None, "sem chave GCP_CHAVE (CNO desligado)"
    try:
        from google.cloud import bigquery
        from google.oauth2 import service_account
    except ImportError:
        return None, "biblioteca google-cloud-bigquery não instalada"
    info = json.loads(chave)
    cred = service_account.Credentials.from_service_account_info(info)
    return bigquery.Client(credentials=cred, project=info["project_id"]), ""


def _colunas(cli):
    q = f"SELECT table_name, column_name FROM `{DATASET}.INFORMATION_SCHEMA.COLUMNS`"
    tabelas = {}
    for r in cli.query(q).result():
        tabelas.setdefault(r.table_name, []).append(r.column_name)
    return tabelas


def _escolher(cols):
    out = {}
    for chave, nomes in CANDIDATAS.items():
        for n in nomes:
            if n in cols:
                out[chave] = n
                break
    return out


SITUACAO = {"1": "Nula", "2": "Ativa", "3": "Suspensa", "14": "Paralisada", "15": "Encerrada"}
QUALIFICACAO = {"53": "Construtora", "64": "Incorporadora", "57": "Dono da obra", "70": "Proprietário do imóvel",
                "109": "Consórcio", "110": "Construção em nome coletivo", "111": "Líder de consórcio"}
INICIO_DESDE_DIAS = 540   # obras iniciadas até ~18 meses antes da foto do CNO
AREA_MIN_ATIVA = 500      # m²


def buscar(municipios_alvo, hoje=None, log=print):
    """municipios_alvo: {ibge: {nome, uf, lat, lon}}. Devolve (lista de registros, relatório).

    A Base dos Dados guarda "fotos" diárias do CNO (coluna data_extracao). Usamos só a foto
    mais recente e só obras ATIVAS, grandes e iniciadas nos ~18 meses anteriores à foto:
    são as que mais provavelmente ainda estão em obra (estrutura, pisos, calçadas).
    """
    hoje = hoje or date.today()
    cli, erro = _cliente()
    if not cli:
        log(f"CNO: {erro}")
        return [], {"status": "desligado", "motivo": erro}
    from google.cloud import bigquery
    T = f"{DATASET}.microdados"
    foto = list(cli.query(f"SELECT MAX(data_extracao) d FROM `{T}` WHERE data_extracao >= '2024-01-01'").result())[0].d
    desde = (foto - timedelta(days=INICIO_DESDE_DIAS)).isoformat()
    q = f"""
      SELECT id_cno AS id, nome_empresarial AS nome, sigla_uf AS uf, id_municipio AS municipio, cep,
             tipo_logradouro, logradouro, numero_logradouro AS numero, bairro, complemento,
             area, unidade_medida, CAST(data_inicio AS STRING) AS inicio, CAST(data_registro AS STRING) AS registro,
             situacao, id_responsavel AS responsavel, qualificacao_responsavel AS qualificacao
      FROM `{T}`
      WHERE data_extracao = DATE(@foto)
        AND sigla_uf IN UNNEST(@ufs) AND id_municipio IN UNNEST(@codigos)
        AND situacao = '2' AND data_inicio >= DATE(@desde)
        AND area >= {AREA_MIN_ATIVA} AND LOWER(IFNULL(unidade_medida, 'm2')) LIKE 'm%'
      ORDER BY area DESC
      LIMIT {MAX_OBRAS}"""
    cfg = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("foto", "STRING", foto.isoformat()),
        bigquery.ArrayQueryParameter("ufs", "STRING", sorted({v["uf"] for v in municipios_alvo.values()})),
        bigquery.ArrayQueryParameter("codigos", "STRING", sorted({str(k) for k in municipios_alvo})),
        bigquery.ScalarQueryParameter("desde", "STRING", desde)])
    job = cli.query(q, job_config=cfg)
    linhas = [dict(r.items()) for r in job.result()]
    for r in linhas:
        r["foto"] = foto.isoformat()
    gb = (job.total_bytes_processed or 0) / 1e9
    atraso = (hoje - foto).days
    log(f"CNO: foto de {foto} ({atraso} dias atrás); {len(linhas)} obras ativas ≥{AREA_MIN_ATIVA} m² iniciadas desde {desde} ({gb:.2f} GB lidos)")
    return linhas, {"status": "ok", "foto": foto.isoformat(), "dias_de_atraso": atraso, "obras": len(linhas), "gb": round(gb, 2)}


def _data_br(s):
    s = str(s or "")[:10]
    return "/".join(reversed(s.split("-"))) if re.match(r"\d{4}-\d{2}-\d{2}", s) else s


def converter(r, municipios_alvo, hoje, dados_cnpj=None):
    try:
        mun = municipios_alvo.get(int(str(r.get("municipio"))[:7]))
    except ValueError:
        mun = None
    if not mun:
        return None
    try:
        area = float(r.get("area") or 0)
    except ValueError:
        area = 0.0
    nome_txt = _norm(r.get("nome"))
    ni = re.sub(r"\D", "", str(r.get("responsavel") or ""))
    pj = len(ni) == 14
    if re.search(r"galp|logist|industri|armaz|deposito|fabrica|pavilh", nome_txt):
        cat = "industrial"
    elif area >= 1000 or re.search(r"condom|residencial|edific|torre|incorpora|spe\b", nome_txt):
        cat = "predio"
    else:
        cat = "residencial"
    porte = "grande" if area >= 5000 else "medio" if area >= 1000 else "pequeno"
    ini = str(r.get("inicio") or r.get("registro") or "")[:10]
    recente = bool(ini) and ini >= (hoje - timedelta(days=365)).isoformat()
    prioridade = "alta" if area >= 3000 and recente else "media" if area >= 1500 or (area >= 800 and recente) else "baixa"
    rua = " ".join(x for x in [str(r.get("tipo_logradouro") or "").title(), str(r.get("logradouro") or "").title()] if x).strip()
    num = str(r.get("numero") or "").strip()
    bairro = str(r.get("bairro") or "").title()
    endereco = rua + (f", {num}" if num and num.upper() not in ("SN", "S/N", "0") else "") + (f" – {bairro}" if bairro else "")
    nome = str(r.get("nome") or "").strip().title() if pj else ""
    obra = dict(
        id=f"cno-{r.get('id')}",
        nome=(nome or "Obra particular") + f" – {int(area):,} m²".replace(",", "."),
        municipio=mun["nome"], uf=mun["uf"], endereco=endereco, cep=str(r.get("cep") or ""),
        categoria=cat, fase="em_obra",
        fase_texto=(f"Ativa no CNO em {_data_br(r.get('foto'))} – início {_data_br(ini)}" if ini else "Ativa no CNO (Receita)"),
        prioridade=prioridade, porte=porte, valor=f"{int(area):,} m² de área".replace(",", "."),
        responsavel=(nome if pj else "Pessoa física") + (f" ({QUALIFICACAO.get(str(r.get('qualificacao')), '')})" if QUALIFICACAO.get(str(r.get('qualificacao'))) else ""),
        construtora=nome if pj and (str(r.get("qualificacao")) in ("53", "64") or re.search(r"constru|engenh|incorpor|empreend", nome_txt)) else "",
        contato="", previsao="",
        dica=("Obra grande iniciada há menos de 1 ano: estrutura e pisos devem estar em andamento. Vá ao endereço e procure o engenheiro ou o mestre de obras."
              if recente else "Obra ativa há mais de 1 ano: confira no local a fase (ainda pode ter lajes, pisos, calçadas e muros)."),
        fonte="https://basedosdados.org/dataset/062621e9-5aa0-4903-852d-619ae54393d2",
        data_fonte=_data_br(ini), data_iso=ini, origem="CNO – Receita Federal (automático)",
        cno=str(r.get("id") or ""), area_m2=area,
    )
    if pj and dados_cnpj and prioridade in ("alta", "media"):
        info = dados_cnpj(ni) or {}
        obra["contato"] = " / ".join(x for x in [" / ".join(info.get("telefones") or []), info.get("email")] if x)
        if info.get("fantasia") or info.get("razao"):
            obra["responsavel"] = info.get("fantasia") or info.get("razao")
    return obra


def cruzar(cno_obras, outras):
    """Marca obras que já estavam na lista e aparecem no CNO (mesma rua+número ou mesmo CNPJ)."""
    def chave(o):
        e = _norm(o.get("endereco"))
        m = re.search(r"([a-z][a-z .]{3,}?)\s*,\s*(\d{1,5})", e)
        if not m:
            return None
        rua = re.sub(r"\b(rua|r|avenida|av|estrada|estr|travessa|tv|alameda|al|rodovia)\b\.?", " ", m.group(1))
        rua = re.sub(r"\s+", " ", rua).strip()
        return (rua[-18:], m.group(2), _norm(o.get("municipio")).split("/")[0].strip())
    idx = {}
    for c in cno_obras:
        k = chave(c)
        if k:
            idx[k] = c
    achadas = 0
    for o in outras:
        k = chave(o)
        if k and k in idx:
            o["cno"] = idx[k]["cno"]
            o["cno_area_m2"] = idx[k].get("area_m2")
            idx[k]["duplicada_de"] = o["id"]
            achadas += 1
    return achadas
