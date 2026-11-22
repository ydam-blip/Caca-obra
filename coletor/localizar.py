"""
Localiza cada obra no mapa pelo endereço (e não pelo centro da cidade) e acha um
ponto de referência perto dela.

- Endereço: usa o campo "endereco" da obra; se estiver vazio, tenta tirar a rua do
  texto da licitação ("pavimentação da Rua Maurício Cardoso…").
- Coordenadas: OpenStreetMap (Nominatim), 1 consulta por segundo, com cache em
  dados/geocache.json para não repetir a mesma busca toda semana.
- Conferência: o ponto achado tem de ficar a até 30 km do centro do município da
  obra; se não ficar, é descartado e a obra fica marcada como "só a cidade".
- Referência: o lugar com nome mais perto do ponto (escola, igreja, mercado, posto,
  praça, hospital…) num raio de 400 m, via Overpass (OpenStreetMap).

Cada obra ganha: lat, lon, precisao ("rua" = ponto exato | "via" = rua sem número | "bairro" | "cidade"), bairro, referencia.
"""
import json, math, os, re, time, unicodedata, urllib.parse, urllib.request

NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"
UA = "caca-obra-coletor/1.0"
MAX_KM_DO_CENTRO = 30

RE_LOGRADOURO = re.compile(
    r"\b(rua|r\.|avenida|av\.?|estrada|travessa|alameda|rodovia|beco|largo|pra[cç]a)\s+"
    r"([A-ZÀ-Úa-zà-ú0-9º°'\. ]{3,60}?)"
    r"(?:\s*,?\s*(?:n[º°o\.]*\s*)?(\d{1,5}))?(?=\s*[,;\-–(]|\s+(?:no|na|em|bairro|localizad|situad|entre|com|do munic|pel[ao]|para|sobre|que|totalizando|até|ate|desde|trecho)\b|$)",
    re.I)
RE_BAIRRO = re.compile(r"\bbairro\s+([A-ZÀ-Úa-zà-ú' ]{3,40}?)(?=\s*[,;\-–.(]|\s+(?:no|na|em|munic)|$)", re.I)


def _norm(s):
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()


def _dist(a, b, c, d):
    r = math.pi / 180
    x = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 2 * 6371 * math.asin(math.sqrt(x))


def _get(url, dados=None):
    for i in range(3):
        try:
            req = urllib.request.Request(url, data=dados, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.loads(r.read())
        except Exception as e:
            print(f"   aviso localizar: {e}")
            time.sleep(5 * (i + 1))
    return None


def endereco_do_texto(texto):
    """Tira 'Rua X, 123' / 'Bairro Y' do texto da licitação."""
    t = re.sub(r"\s+", " ", texto or "")
    partes = []
    m = RE_LOGRADOURO.search(t)
    if m:
        tipo = m.group(1).rstrip(".").lower()
        tipo = {"r": "Rua", "av": "Avenida"}.get(tipo, tipo.capitalize())
        nome = m.group(2).strip(" .,")
        partes.append(f"{tipo} {nome}" + (f", {m.group(3)}" if m.group(3) else ""))
    b = RE_BAIRRO.search(t)
    if b:
        partes.append("Bairro " + b.group(1).strip())
    return " – ".join(partes)


def _consultas(end, municipio, uf):
    """Variações do endereço, da mais exata para a mais solta."""
    end = re.sub(r"\(.*?\)", "", end or "")
    end = re.sub(r"(?<=\d)\.(?=\d{3})", "", end)                      # 2.210 -> 2210
    end = re.sub(r"\b(perto|pr[oó]xim[oa]s?|margens?|entre|divisa)\s+(d[aeo]s?|a|com)?\s*", "", end, flags=re.I)
    pedacos = [p.strip() for p in re.split(r"[–;/]| - |,(?=\s*[A-ZÀ-Ú][a-zà-ú]+\s*(?:$|–))", end) if p.strip()]
    vistas, out = set(), []
    for q in [end] + pedacos:
        q = re.sub(r"^bairro\s+", "", q.strip(), flags=re.I)
        if len(q) < 3 or _norm(q) in vistas:
            continue
        if re.search(r"nao detalhad|varios bairros|nao informad|^zona (norte|sul|leste|oeste)$", _norm(q)):
            continue
        vistas.add(_norm(q))
        out.append(f"{q}, {municipio}, {uf}, Brasil")
    return out[:4]


def _geocodificar(q, cache):
    if q in cache:
        return cache[q]
    url = NOMINATIM + "?" + urllib.parse.urlencode(dict(q=q, format="jsonv2", limit=1, countrycodes="br", addressdetails=1))
    res = _get(url)
    time.sleep(1.1)  # regra de uso do Nominatim: no máximo 1 por segundo
    achado = None
    if res:
        r = res[0]
        a = r.get("address", {})
        cat, tipo, atipo = r.get("category") or "", r.get("type") or "", r.get("addresstype") or ""
        if a.get("house_number") or cat in ("building", "amenity", "shop", "office", "tourism", "leisure", "man_made", "craft") \
                or atipo in ("house", "building", "amenity", "shop", "industrial", "commercial"):
            prec = "rua"          # ponto exato (número ou lugar com nome)
        elif cat in ("highway", "railway") or atipo == "road":
            prec = "via"          # a rua certa, mas sem o número
        elif cat == "landuse" or atipo in ("suburb", "neighbourhood", "quarter", "city_district", "hamlet", "village",
                                           "isolated_dwelling", "residential", "allotments"):
            prec = "bairro"
        else:
            prec = "cidade"
        achado = dict(lat=float(r["lat"]), lon=float(r["lon"]), precisao=prec,
                      bairro=a.get("suburb") or a.get("neighbourhood") or a.get("quarter") or a.get("city_district") or "",
                      rua=a.get("road") or "")
    cache[q] = achado
    return achado


def _referencia(lat, lon, cache):
    chave = f"{lat:.4f},{lon:.4f}"
    if chave in cache:
        return cache[chave]
    q = f"""[out:json][timeout:25];
(node(around:400,{lat},{lon})[name][amenity~"school|place_of_worship|hospital|clinic|fuel|marketplace|police|fire_station|townhall|university|college|bank|kindergarten"];
 node(around:400,{lat},{lon})[name][shop~"supermarket|mall"];
 way(around:400,{lat},{lon})[name][leisure~"park|stadium|sports_centre"];
 way(around:400,{lat},{lon})[name][amenity~"school|hospital|university|fuel"];
 way(around:400,{lat},{lon})[name][shop~"supermarket|mall"];);
out center 30;"""
    res = _get(OVERPASS, urllib.parse.urlencode({"data": q}).encode())
    time.sleep(1.0)
    ref = ""
    if res and res.get("elements"):
        melhor = None
        for e in res["elements"]:
            la = e.get("lat") or (e.get("center") or {}).get("lat")
            lo = e.get("lon") or (e.get("center") or {}).get("lon")
            if la is None:
                continue
            d = _dist(lat, lon, la, lo) * 1000
            if melhor is None or d < melhor[0]:
                melhor = (d, e["tags"]["name"])
        if melhor:
            ref = f"perto de {melhor[1]} (~{int(round(melhor[0], -1))} m)"
    cache[chave] = ref
    return ref


def _aceitar(r, centro, mun_nome):
    return (r and centro and _dist(centro["lat"], centro["lon"], r["lat"], r["lon"]) <= MAX_KM_DO_CENTRO
            and r["precisao"] != "cidade"
            and not (r["precisao"] == "bairro" and _norm(r["bairro"] or "") in ("", _norm(mun_nome))))


def localizar(obras, municipios_por_nome, pasta_dados, limite=500, texto_extra=None, endereco_orgao=None):
    """Atualiza lat/lon/precisao/bairro/referencia das obras. Devolve contagem por precisão.

    Ordem de tentativa:
      1. endereço da obra (campo endereco ou rua citada no nome da licitação);
      2. rua citada nos itens/observações da licitação (texto_extra(o) -> texto);
      3. endereço do órgão que fez a licitação (endereco_orgao(o) -> dict(texto, consulta, nome)),
         marcado como precisao "orgao" para o app avisar que não é o local da obra;
      4. centro da cidade (precisao "cidade").
    """
    caminho = os.path.join(pasta_dados, "geocache.json")
    cache = {}
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            cache = json.load(f)
    if cache.get("versao") != 2:
        cache = {"versao": 2}
    geo, refs = cache.setdefault("geo", {}), cache.setdefault("ref", {})
    extras, orgaos = cache.setdefault("extra", {}), cache.setdefault("orgao", {})
    feitas = 0
    contagem = {"rua": 0, "via": 0, "bairro": 0, "orgao": 0, "cidade": 0}

    def tentar(end, mun_nome, uf, centro):
        nonlocal feitas
        if not (end and centro) or feitas >= limite:
            return None
        for q in _consultas(end, mun_nome, uf):
            novo = q not in geo
            r = _geocodificar(q, geo)
            feitas += 1 if novo else 0
            if _aceitar(r, centro, mun_nome):
                return r
        return None

    for o in obras:
        if o.get("precisao") in ("rua", "via", "bairro") and o.get("lat") and o.get("geo_ok"):
            contagem[o["precisao"]] += 1
            continue
        uf = o.get("uf") or "RS"
        mun_nome = (o.get("municipio") or "").split("/")[0].strip()
        centro = municipios_por_nome.get((_norm(mun_nome), uf))
        end = o.get("endereco") or ""
        if not end or _norm(end) == _norm(mun_nome):
            end = endereco_do_texto(o.get("nome", ""))
            if end and not o.get("endereco"):
                o["endereco"] = end
        resultado = tentar(end, mun_nome, uf, centro)

        # 2. itens e observações da licitação
        if not resultado and texto_extra and o.get("id", "").startswith("pncp-"):
            if o["id"] not in extras:
                extras[o["id"]] = endereco_do_texto(texto_extra(o) or "")
            end2 = extras[o["id"]]
            if end2:
                resultado = tentar(end2, mun_nome, uf, centro)
                if resultado and not o.get("endereco"):
                    o["endereco"] = end2

        if resultado:
            o.update(lat=round(resultado["lat"], 5), lon=round(resultado["lon"], 5), precisao=resultado["precisao"],
                     bairro=resultado["bairro"], geo_ok=True)
            o.pop("local_orgao", None)
            if resultado["precisao"] in ("rua", "via"):
                o["referencia"] = _referencia(resultado["lat"], resultado["lon"], refs)
            contagem[o["precisao"]] += 1
            continue

        # 3. endereço do órgão comprador
        if endereco_orgao and o.get("id", "").startswith("pncp-"):
            cnpj = o.get("orgao_cnpj") or (re.match(r"pncp-(\d{14})", o["id"]) or [None, None])[1]
            if cnpj and cnpj not in orgaos:
                info = endereco_orgao(cnpj) or {}
                r = None
                if info.get("consulta"):
                    r = tentar(info["consulta"], mun_nome, uf, centro)
                orgaos[cnpj] = dict(texto=info.get("texto", ""), nome=info.get("nome", ""),
                                    lat=r["lat"] if r else None, lon=r["lon"] if r else None)
            og = orgaos.get(cnpj) if cnpj else None
            if og and og.get("lat") is not None:
                o.update(lat=round(og["lat"], 5), lon=round(og["lon"], 5), precisao="orgao",
                         local_orgao=f"{og['nome']} – {og['texto']}".strip(" –"))
                contagem["orgao"] += 1
                continue

        # 4. centro da cidade
        o["precisao"] = "cidade"
        if centro:
            o["lat"], o["lon"] = round(centro["lat"], 4), round(centro["lon"], 4)
        contagem["cidade"] += 1
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    return contagem
