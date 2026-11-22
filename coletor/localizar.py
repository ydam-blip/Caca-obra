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


def localizar(obras, municipios_por_nome, pasta_dados, limite=400):
    """Atualiza lat/lon/precisao/bairro/referencia das obras. Devolve contagem por precisão."""
    caminho = os.path.join(pasta_dados, "geocache.json")
    cache = {}
    if os.path.exists(caminho):
        with open(caminho, encoding="utf-8") as f:
            cache = json.load(f)
    if cache.get("versao") != 2:
        cache = {"versao": 2}
    geo, refs = cache.setdefault("geo", {}), cache.setdefault("ref", {})
    feitas = 0
    contagem = {"rua": 0, "via": 0, "bairro": 0, "cidade": 0}
    for o in obras:
        if o.get("precisao") in ("rua", "via", "bairro") and o.get("lat") and o.get("geo_ok"):
            contagem[o["precisao"]] += 1
            continue
        mun_nome = (o.get("municipio") or "").split("/")[0].strip()
        centro = municipios_por_nome.get((_norm(mun_nome), o.get("uf") or "RS"))
        end = o.get("endereco") or ""
        if not end or _norm(end) == _norm(mun_nome):
            end = endereco_do_texto(o.get("nome", ""))
            if end and not o.get("endereco"):
                o["endereco"] = end
        resultado = None
        if end and centro and feitas < limite:
            for q in _consultas(end, mun_nome, o.get("uf") or "RS"):
                novo = q not in geo
                r = _geocodificar(q, geo)
                feitas += 1 if novo else 0
                if r and _dist(centro["lat"], centro["lon"], r["lat"], r["lon"]) <= MAX_KM_DO_CENTRO and r["precisao"] != "cidade" \
                        and not (r["precisao"] == "bairro" and _norm(r["bairro"] or "") in ("", _norm(mun_nome))):
                    resultado = r
                    break
        if resultado:
            o.update(lat=round(resultado["lat"], 5), lon=round(resultado["lon"], 5), precisao=resultado["precisao"],
                     bairro=resultado["bairro"], geo_ok=True)
            if resultado["precisao"] in ("rua", "via"):
                o["referencia"] = _referencia(resultado["lat"], resultado["lon"], refs)
        else:
            o["precisao"] = "cidade"
            if centro:
                o["lat"], o["lon"] = round(centro["lat"], 4), round(centro["lon"], 4)
        contagem[o["precisao"]] += 1
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    return contagem
