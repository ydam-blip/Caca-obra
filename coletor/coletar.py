#!/usr/bin/env python3
"""
Coletor automático do Caça Obra.

O que faz, para cada região em coletor/regioes.json (cidade + raio em km):
  1. Busca no PNCP (Portal Nacional de Contratações Públicas) as licitações de
     obras e serviços de engenharia dos municípios dentro do raio:
       - com proposta aberta (ainda dá tempo de a construtora entrar);
       - publicadas nos últimos DIAS_PUBLICADAS dias.
  2. Para licitações já encerradas, tenta descobrir a empresa vencedora.
  3. Busca telefone, e-mail e endereço da vencedora pelo CNPJ (BrasilAPI,
     dados públicos da Receita Federal).
  4. Junta com as obras da pesquisa manual (dados/obras_piloto.json e
     dados/obras_manuais.json) e grava dados/obras.json, que o aplicativo lê.

Só usa a biblioteca padrão do Python (roda no GitHub Actions sem instalar nada).

Uso:
  python3 coletor/coletar.py                      # todas as regiões do regioes.json
  python3 coletor/coletar.py --cidade "Passo Fundo" --uf RS --raio 80   # região avulsa (é salva no regioes.json)
"""
import argparse, csv, json, math, os, re, sys, time, unicodedata, urllib.parse, urllib.request
from datetime import date, datetime, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(BASE, "dados")
COLETOR = os.path.join(BASE, "coletor")

PNCP_CONSULTA = "https://pncp.gov.br/api/consulta/v1"
PNCP_API = "https://pncp.gov.br/api/pncp/v1"
BRASILAPI_CNPJ = "https://brasilapi.com.br/api/cnpj/v1/"

# Modalidades do PNCP que costumam trazer obras: 4 Concorrência eletrônica,
# 5 Concorrência presencial, 6 Pregão eletrônico, 8 Dispensa (obras pequenas).
MODALIDADES = [4, 5, 6, 8]
DIAS_PUBLICADAS = 30
DIAS_MANTER = 180          # licitações encerradas há mais tempo saem da lista
TAM_PAGINA = 50
MAX_PAGINAS = 60           # trava de segurança por consulta

# Palavras que indicam obra / uso de concreto no objeto da licitação.
PALAVRAS_OBRA = [
    "obra", "construcao", "construir", "edificacao", "pavimentacao", "pavimento", "recapeamento",
    "asfalt", "calcamento", "calcada", "passeio publico", "meio-fio", "meio fio", "drenagem", "pluvial",
    "galeria", "bueiro", "ponte", "viaduto", "passarela", "contencao", "muro", "reforma", "ampliacao",
    "concreto", "usinado", "pre-moldado", "pre moldado", "laje", "fundacao", "estaca", "terraplenagem",
    "saneamento", "esgot", "estacao de tratamento", "reservatorio", "escola", "creche", "ubs",
    "unidade basica", "quadra", "ginasio", "pista", "ciclovia", "praca", "habitacional", "moradias",
    "unidades habitacionais", "engenharia", "infraestrutura", "urbanizacao", "dique", "barragem",
]
# Palavras que tiram o item da lista (compras que não são obra).
PALAVRAS_EXCLUIR = [
    "aquisicao de pneus", "medicamento", "genero alimenticio", "generos alimenticios", "merenda",
    "locacao de veiculo", "combustivel", "material de expediente", "software", "licenca de uso",
    "transporte escolar", "uniforme", "consultoria juridica", "plano de saude", "limpeza de fossa",
    "servicos de limpeza", "coleta de lixo", "vigilancia", "seguro ", "passagens aereas",
]
PALAVRAS_INFRA = ["pavimenta", "recape", "asfalt", "calcamento", "drenagem", "pluvial", "galeria", "bueiro",
                  "ponte", "viaduto", "saneamento", "esgot", "estrada", "rodovia", "terraplen", "dique",
                  "barragem", "contencao", "meio-fio", "meio fio", "ciclovia", "infraestrutura", "urbanizacao"]
PALAVRAS_CONCRETO = ["concreto usinado", "concreto", "usinado", "pre-moldado", "artefatos de cimento", "bloco de concreto", "paver"]


def norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


# ---------------------------------------------------------------- HTTP
def http_json(url, tentativas=3):
    """Baixa JSON. Devolve None se não houver conteúdo ou se der erro."""
    for i in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "caca-obra-coletor/1.0"})
            with urllib.request.urlopen(req, timeout=40) as r:
                if r.status == 204:
                    return None
                corpo = r.read()
                return json.loads(corpo) if corpo else None
        except urllib.error.HTTPError as e:
            if e.code in (204, 400, 404, 422):
                return None
            if e.code == 429:
                time.sleep(10 * (i + 1))
                continue
            time.sleep(3 * (i + 1))
        except Exception as e:  # rede, timeout, JSON inválido
            print(f"   aviso: falha em {url[:120]}… ({e})", file=sys.stderr)
            time.sleep(3 * (i + 1))
    return None


# ---------------------------------------------------------------- Regiões
def carregar_municipios():
    UF = {'11': 'RO', '12': 'AC', '13': 'AM', '14': 'RR', '15': 'PA', '16': 'AP', '17': 'TO', '21': 'MA', '22': 'PI',
          '23': 'CE', '24': 'RN', '25': 'PB', '26': 'PE', '27': 'AL', '28': 'SE', '29': 'BA', '31': 'MG', '32': 'ES',
          '33': 'RJ', '35': 'SP', '41': 'PR', '42': 'SC', '43': 'RS', '50': 'MS', '51': 'MT', '52': 'GO', '53': 'DF'}
    out = []
    with open(os.path.join(COLETOR, "municipios_ibge.csv"), encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.append(dict(ibge=int(r["codigo_ibge"]), nome=r["nome"], uf=UF[r["codigo_uf"]],
                            lat=float(r["latitude"]), lon=float(r["longitude"])))
    return out


def distancia_km(a_lat, a_lon, b_lat, b_lon):
    R = 6371.0
    p1, p2 = math.radians(a_lat), math.radians(b_lat)
    dp, dl = p2 - p1, math.radians(b_lon - a_lon)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def municipios_no_raio(municipios, cidade, uf, raio):
    centro = next((m for m in municipios if norm(m["nome"]) == norm(cidade) and m["uf"] == uf.upper()), None)
    if not centro:
        raise SystemExit(f"Cidade não encontrada: {cidade}/{uf}")
    return centro, {m["ibge"]: m for m in municipios
                    if distancia_km(centro["lat"], centro["lon"], m["lat"], m["lon"]) <= raio}


# ---------------------------------------------------------------- PNCP
def paginar(endpoint, params):
    itens, pagina = [], 1
    while pagina <= MAX_PAGINAS:
        q = dict(params, pagina=pagina, tamanhoPagina=TAM_PAGINA)
        dados = http_json(f"{PNCP_CONSULTA}/{endpoint}?{urllib.parse.urlencode(q)}")
        if not dados or not dados.get("data"):
            break
        itens.extend(dados["data"])
        if not dados.get("paginasRestantes"):
            break
        pagina += 1
        time.sleep(0.3)
    return itens


def buscar_pncp(ufs, hoje):
    """Devolve dict numeroControlePNCP -> item, para as UFs pedidas."""
    achados = {}
    ini = (hoje - timedelta(days=DIAS_PUBLICADAS)).strftime("%Y%m%d")
    fim_pub = hoje.strftime("%Y%m%d")
    fim_prop = (hoje + timedelta(days=120)).strftime("%Y%m%d")
    for uf in sorted(ufs):
        for mod in MODALIDADES:
            print(f"  PNCP {uf} modalidade {mod}: propostas abertas…")
            for it in paginar("contratacoes/proposta", dict(dataFinal=fim_prop, codigoModalidadeContratacao=mod, uf=uf)):
                achados[it.get("numeroControlePNCP")] = it
            print(f"  PNCP {uf} modalidade {mod}: publicadas desde {ini}…")
            for it in paginar("contratacoes/publicacao", dict(dataInicial=ini, dataFinal=fim_pub, codigoModalidadeContratacao=mod, uf=uf)):
                achados.setdefault(it.get("numeroControlePNCP"), it)
    return achados


def e_obra(objeto):
    o = norm(objeto)
    if any(p in o for p in PALAVRAS_EXCLUIR):
        return False
    return any(p in o for p in PALAVRAS_OBRA)


def buscar_vencedor(item):
    """Tenta achar o CNPJ do vencedor do item 1 da compra (melhor esforço)."""
    cnpj = (item.get("orgaoEntidade") or {}).get("cnpj")
    ano, seq = item.get("anoCompra"), item.get("sequencialCompra")
    if not (cnpj and ano and seq):
        return None
    res = http_json(f"{PNCP_API}/orgaos/{cnpj}/compras/{ano}/{seq}/itens/1/resultados", tentativas=1)
    if isinstance(res, list) and res:
        r = res[0]
        return dict(cnpj=re.sub(r"\D", "", str(r.get("niFornecedor") or "")),
                    nome=r.get("nomeRazaoSocialFornecedor") or "")
    return None


_cache_cnpj = {}


def dados_cnpj(cnpj):
    """Telefone, e-mail e endereço públicos da empresa (Receita, via BrasilAPI)."""
    if not cnpj or len(cnpj) != 14:
        return None
    if cnpj in _cache_cnpj:
        return _cache_cnpj[cnpj]
    d = http_json(BRASILAPI_CNPJ + cnpj, tentativas=2)
    info = None
    if d:
        tels = []
        for k in ("ddd_telefone_1", "ddd_telefone_2"):
            t = re.sub(r"\D", "", str(d.get(k) or ""))
            if len(t) >= 10:
                tels.append(f"({t[:2]}) {t[2:-4]}-{t[-4:]}")
        end = ", ".join(x for x in [
            " ".join(x for x in [d.get("descricao_tipo_de_logradouro") or "", d.get("logradouro") or ""] if x).strip(),
            d.get("numero") or "", d.get("bairro") or "",
            f"{d.get('municipio') or ''}/{d.get('uf') or ''}"] if x and x != "/")
        info = dict(razao=d.get("razao_social") or "", fantasia=d.get("nome_fantasia") or "",
                    telefones=tels, email=(d.get("email") or "").lower(), endereco=end,
                    atividade=d.get("cnae_fiscal_descricao") or "")
    _cache_cnpj[cnpj] = info
    time.sleep(0.5)
    return info


def converter(item, muni, hoje):
    objeto = (item.get("objetoCompra") or "").strip()
    o = norm(objeto)
    valor = item.get("valorTotalEstimado") or item.get("valorTotalHomologado") or 0
    try:
        valor = float(valor)
    except (TypeError, ValueError):
        valor = 0.0
    porte = "grande" if valor >= 5_000_000 else "medio" if valor >= 500_000 else "pequeno"
    compra_concreto = any(p in o for p in PALAVRAS_CONCRETO)
    categoria = "infraestrutura" if any(p in o for p in PALAVRAS_INFRA) else "obra_publica"
    enc = (item.get("dataEncerramentoProposta") or "")[:10]
    aberta = bool(enc) and enc >= hoje.isoformat()
    if compra_concreto:
        prioridade = "alta"
    elif porte == "grande" or (porte == "medio" and aberta):
        prioridade = "alta" if porte == "grande" else "media"
    elif porte == "medio":
        prioridade = "media"
    else:
        prioridade = "baixa"
    org = item.get("orgaoEntidade") or {}
    uni = item.get("unidadeOrgao") or {}
    nctrl = item.get("numeroControlePNCP") or ""
    m = re.match(r"(\d{14})-\d-(\d+)/(\d{4})", nctrl)
    link = f"https://pncp.gov.br/app/editais/{m.group(1)}/{m.group(3)}/{int(m.group(2))}" if m else (item.get("linkSistemaOrigem") or "https://pncp.gov.br")
    if aberta:
        fase_txt = f"Licitação aberta – propostas até {enc[8:10]}/{enc[5:7]}/{enc[:4]}"
    elif enc:
        fase_txt = f"Licitação encerrada em {enc[8:10]}/{enc[5:7]}/{enc[:4]} – {item.get('situacaoCompraNome') or 'ver resultado'}"
    else:
        fase_txt = item.get("situacaoCompraNome") or "Licitação publicada"
    dica = ("Compra direta de concreto/pré-moldado: o órgão é o comprador. Participe ou procure quem venceu."
            if compra_concreto else
            "Licitação aberta: procure as construtoras que estão disputando e ofereça orçamento para a proposta delas."
            if aberta else
            "Descubra quem venceu e ofereça concreto para o início da obra.")
    return dict(
        id="pncp-" + nctrl,
        nome=objeto[:220],
        municipio=uni.get("municipioNome") or muni["nome"],
        uf=uni.get("ufSigla") or muni["uf"],
        ibge=muni["ibge"], lat=round(muni["lat"], 3), lon=round(muni["lon"], 3),
        endereco="", categoria=categoria,
        fase="licitacao", fase_texto=fase_txt,
        prioridade=prioridade,
        responsavel=org.get("razaoSocial") or uni.get("nomeUnidade") or "",
        construtora="A definir (licitação)" if aberta else "",
        contato="", porte=porte,
        valor=(f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " (estimado)") if valor else "",
        previsao="", dica=dica, fonte=link,
        data_fonte=(item.get("dataPublicacaoPncp") or "")[:10],
        encerramento=enc, origem="PNCP (automático)", modalidade=item.get("modalidadeNome") or "",
        _compra=item,
    )


# ---------------------------------------------------------------- Junção
def ler_json(nome, padrao):
    caminho = os.path.join(DADOS, nome)
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            return json.load(f)
    return padrao


def gravar_json(nome, obj, compacto=False):
    caminho = os.path.join(DADOS, nome)
    tmp = caminho + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        if compacto:
            json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
        else:
            json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, caminho)


def coletar(regioes, hoje=None, buscar=buscar_pncp, vencedor=buscar_vencedor, cnpj=dados_cnpj):
    hoje = hoje or date.today()
    municipios = carregar_municipios()
    alvo = {}
    for r in regioes:
        centro, mun = municipios_no_raio(municipios, r["cidade"], r["uf"], float(r["raio_km"]))
        print(f"Região {r['cidade']}/{r['uf']} {r['raio_km']} km: {len(mun)} municípios")
        alvo.update(mun)
    ufs = {m["uf"] for m in alvo.values()}
    brutos = buscar(ufs, hoje)

    anteriores = {o["id"]: o for o in ler_json("obras_coletadas.json", [])}
    novos = {}
    for nctrl, it in brutos.items():
        if not nctrl:
            continue
        ibge = (it.get("unidadeOrgao") or {}).get("codigoIbge")
        try:
            ibge = int(ibge)
        except (TypeError, ValueError):
            continue
        if ibge not in alvo or not e_obra(it.get("objetoCompra")):
            continue
        ob = converter(it, alvo[ibge], hoje)
        antes = anteriores.get(ob["id"], {})
        ob["primeira_vez"] = antes.get("primeira_vez") or hoje.isoformat()
        # vencedor + contato (só para encerradas; reaproveita o que já sabíamos)
        if antes.get("construtora") and antes.get("construtora") not in ("", "A definir (licitação)"):
            for k in ("construtora", "contato", "cnpj_construtora", "endereco_construtora"):
                if antes.get(k):
                    ob[k] = antes[k]
        elif ob["encerramento"] and ob["encerramento"] < hoje.isoformat():
            v = vencedor(ob["_compra"])
            if v and v.get("cnpj"):
                info = cnpj(v["cnpj"]) or {}
                ob["construtora"] = info.get("fantasia") or info.get("razao") or v.get("nome") or ""
                ob["cnpj_construtora"] = v["cnpj"]
                ob["contato"] = " / ".join(x for x in [" / ".join(info.get("telefones") or []), info.get("email")] if x)
                ob["endereco_construtora"] = info.get("endereco", "")
                ob["fase"] = "contratada"
                ob["fase_texto"] = "Vencedor definido – obra deve começar em breve"
                ob["dica"] = "Ligue para a construtora vencedora e ofereça concreto antes do início da obra."
                if any(p in norm(ob["nome"]) for p in PALAVRAS_CONCRETO):
                    ob["construtora"] = "Fornecedor vencedor (concorrente): " + ob["construtora"]
                    ob["fase_texto"] = "Compra de concreto já contratada"
                    ob["dica"] = "Outro fornecedor venceu. Anote o preço e o prazo do contrato e prepare-se para a próxima compra deste órgão."
        ob.pop("_compra", None)
        novos[ob["id"]] = ob

    # mantém coletadas antigas que ainda valem (fora do raio desta vez ou não retornadas)
    limite = (hoje - timedelta(days=DIAS_MANTER)).isoformat()
    for oid, o in anteriores.items():
        if oid not in novos and (o.get("encerramento") or o.get("data_fonte") or "9999") >= limite:
            novos[oid] = o
    coletadas = sorted(novos.values(), key=lambda o: (o.get("primeira_vez") or "", o.get("data_fonte") or ""), reverse=True)
    gravar_json("obras_coletadas.json", coletadas)

    manuais = ler_json("obras_piloto.json", []) + ler_json("obras_manuais.json", [])
    tudo = manuais + coletadas
    gravar_json("obras.json", dict(
        atualizado_em=datetime.now().strftime("%Y-%m-%d %H:%M"),
        regioes=regioes, total=len(tudo), obras=tudo), compacto=True)
    print(f"OK: {len(coletadas)} obras do PNCP + {len(manuais)} da pesquisa manual = {len(tudo)}")
    return tudo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cidade"); ap.add_argument("--uf"); ap.add_argument("--raio", type=float, default=60)
    a = ap.parse_args()
    cfg_path = os.path.join(COLETOR, "regioes.json")
    with open(cfg_path, encoding="utf-8") as f:
        regioes = json.load(f)
    if a.cidade and a.uf:
        nova = dict(cidade=a.cidade, uf=a.uf.upper(), raio_km=a.raio)
        regioes = [r for r in regioes if norm(r["cidade"]) != norm(a.cidade)] + [nova]
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(regioes, f, ensure_ascii=False, indent=1)
    coletar(regioes)


if __name__ == "__main__":
    main()
