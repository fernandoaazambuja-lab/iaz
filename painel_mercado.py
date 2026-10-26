#!/usr/bin/env python3
"""Painel de Mercado Agro — gera 11_Painel_Mercado/painel_mercado.html.

Roda a cada 2 min pelo launchd (~/Library/LaunchAgents/br.com.schmidtagricola.painelmercado.plist).
Fontes, todas públicas e sem chave:
  - B3 (cotacao.b3.com.br, atraso de 15 min): dólar futuro, DI, milho B3, soja B3, Ibovespa futuro
  - CNBC (quote.cnbc.com): CBOT, ICE, petróleo, câmbio spot, DXY, yuan, EWZ (com pré/pós-mercado)
  - Banco Central (olinda.bcb.gov.br): PTAX
Contrato novo ou troca de vencimento: editar só o bloco PARÂMETROS e rodar de novo.
Se uma fonte falhar, o painel mostra o último valor guardado marcado como "dado antigo".
"""
import datetime
import html
import json
import os
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------- PARÂMETROS
TZ = ZoneInfo("America/Bahia")
BASE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# o launchd não tem acesso ao OneDrive: a cópia agendada roda em ~/PainelMercado e grava lá (PAINEL_OUT)
OUT_DIR = os.environ.get("PAINEL_OUT") or os.path.join(BASE, "11_Painel_Mercado")
OUT_HTML = os.path.join(OUT_DIR, "painel_mercado.html")
HIST = os.path.join(OUT_DIR, "_historico_mercado.json")

# Vencimentos "2027" de referência (safra 26/27)
ALVO_CNBC = {"S": "May'27", "C": "Jul'27", "CT": "Dec'27"}
B3_ALVO = {"DOL": "DOLK27", "CCM": "CCMN27", "SJC": "SJCK27"}
B3_DI = ["DI1F27", "DI1F28"]

CNBC_FIXOS = {
    "usdbrl": "BRL=", "dxy": ".DXY", "cny": "CNY=", "us10": "US10Y", "brent": "@LCO.1",
    "wti": "@CL.1", "ng": "@NG.1", "ibov": ".BVSP", "ewz": "EWZ", "spx": ".SPX", "vix": ".VIX",
    "urea": "@UFV.1",
}
# Fertilizante no porto: preço médio de importação do Comex Stat (MDIC), mensal — FOB + frete ≈ CFR
COMEX_NCM = {"31021010": ("ureia", "Ureia"), "31042090": ("kcl", "KCl"),
             "31054000": ("map", "MAP")}
NCM_SOJA = "12019000"      # soja em grão, exceto para semeadura (exportação)
COMEX_INICIO = "2019-01"  # início da curva histórica (pega o pico de 2022)
ST_T = 0.907185          # short ton -> tonelada métrica (ureia Golfo EUA é cotada em US$/short ton)
BU_SC_SOJA = 2.20462     # 60 kg de soja = 2,20462 bushels
BU_SC_MILHO = 2.36210    # 60 kg de milho = 2,36210 bushels
LB_ARROBA = 33.0693      # 15 kg = 33,0693 lb

UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
MES_EN = {"Jan": "jan", "Feb": "fev", "Mar": "mar", "Apr": "abr", "May": "mai", "Jun": "jun",
          "Jul": "jul", "Aug": "ago", "Sep": "set", "Oct": "out", "Nov": "nov", "Dec": "dez"}
MES_B3 = {"F": "jan", "G": "fev", "H": "mar", "J": "abr", "K": "mai", "M": "jun", "N": "jul",
          "Q": "ago", "U": "set", "V": "out", "X": "nov", "Z": "dez"}

AGORA = datetime.datetime.now(TZ)


# ---------------------------------------------------------------- COLETA
def get_json(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode("utf-8"))


def num(s):
    if s in (None, "", "UNCH", "N/A"):
        return None
    try:
        return float(str(s).replace(",", "").replace("%", "").replace("+", ""))
    except ValueError:
        return None


def contrato_cnbc(nome):
    # "Soybeans (May'27)" -> "mai/27"
    if "(" not in (nome or ""):
        return ""
    m = nome[nome.rfind("(") + 1:nome.rfind(")")]
    return MES_EN.get(m[:3], m[:3]) + "/" + m[-2:]


def iso_cnbc(t):
    # "2026-10-26T08:30:00.000-0400" -> datetime no fuso da Bahia
    return datetime.datetime.fromisoformat(t[:19] + t[-5:-2] + ":" + t[-2:]).astimezone(TZ)


def hora_cnbc(q):
    t = q.get("last_time", "")
    try:
        if "T" in t:
            return iso_cnbc(t)
        return datetime.datetime.strptime(t, "%Y-%m-%d").replace(tzinfo=TZ)
    except ValueError:
        return None


def coleta_cnbc():
    simbolos = list(CNBC_FIXOS.values())
    for raiz in ALVO_CNBC:
        simbolos += ["@%s.%d" % (raiz, i) for i in range(1, 9)]
    url = ("https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol?symbols="
           + urllib.parse.quote("|".join(simbolos), safe="|")
           + "&requestMethod=itv&noform=1&partnerId=2&fund=1&exthrs=1&output=json")
    dados = get_json(url)["FormattedQuoteResult"]["FormattedQuote"]
    por_simb = {q["symbol"]: q for q in dados if q.get("code") == 0}
    out = {}

    def item(q, extra=None):
        last, prev = num(q.get("last")), num(q.get("previous_day_closing"))
        d = {"valor": last, "ant": prev, "min": num(q.get("low")), "max": num(q.get("high")),
             "hora": hora_cnbc(q), "contrato": contrato_cnbc(q.get("name")), "fonte": "CNBC",
             "nota": ""}
        ext = q.get("ExtendedMktQuote")
        if ext and q.get("curmktstatus") in ("PRE_MKT", "POST_MKT") and num(ext.get("last")):
            d["valor"] = num(ext["last"])
            d["nota"] = "pré-mercado" if q["curmktstatus"] == "PRE_MKT" else "pós-mercado"
            d["min"] = d["max"] = None
            try:
                d["hora"] = iso_cnbc(ext["last_time"])
            except (KeyError, ValueError):
                pass
        if (d["min"] or 0) <= 0 or (d["max"] or 0) <= 0:
            d["min"] = d["max"] = None
        if extra:
            d.update(extra)
        return d

    for chave, s in CNBC_FIXOS.items():
        if s in por_simb:
            out[chave] = item(por_simb[s])
    for raiz, alvo in ALVO_CNBC.items():
        if "@%s.1" % raiz in por_simb:
            out[raiz + "_1"] = item(por_simb["@%s.1" % raiz])
        for i in range(1, 9):
            q = por_simb.get("@%s.%d" % (raiz, i))
            if q and alvo in q.get("name", ""):
                out[raiz + "_27"] = item(q)
    return out


def b3_preco(s):
    q = s.get("SctyQtn", {})
    if q.get("curPrc"):
        return q["curPrc"], "último negócio"
    bid = (s.get("buyOffer") or {}).get("price")
    ask = (s.get("sellOffer") or {}).get("price")
    if bid and ask:
        return (bid + ask) / 2, "sem negócio — meio de oferta"
    return None, "sem negócio hoje"


def coleta_b3():
    out = {}
    hoje = AGORA.date().isoformat()
    for ativo in ("DOL", "CCM", "SJC", "DI1", "IND"):
        d = get_json("https://cotacao.b3.com.br/mds/api/v1/DerivativeQuotation/" + ativo)
        hora = datetime.datetime.strptime(d["Msg"]["dtTm"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
        futs = [s for s in d["Scty"] if s["mkt"]["cd"] == "FUT"
                and s["asset"]["AsstSummry"]["mtrtyCode"] >= hoje]
        futs.sort(key=lambda s: s["asset"]["AsstSummry"]["mtrtyCode"])
        por_simb = {s["symb"]: s for s in futs}
        escolha = {}
        if ativo in B3_ALVO and futs:
            escolha[ativo + "_1"] = futs[0]
            if B3_ALVO[ativo] in por_simb:
                escolha[ativo + "_27"] = por_simb[B3_ALVO[ativo]]
        elif ativo == "DI1":
            for sym in B3_DI:
                if sym in por_simb:
                    escolha[sym] = por_simb[sym]
        elif futs:
            escolha[ativo + "_1"] = futs[0]
        for chave, s in escolha.items():
            q = s["SctyQtn"]
            preco, nota = b3_preco(s)
            sym = s["symb"]
            out[chave] = {
                "valor": preco, "ant": q.get("prvsDayAdjstmntPric"), "min": q.get("minPric"),
                "max": q.get("maxPric"), "hora": hora, "fonte": "B3",
                "contrato": MES_B3.get(sym[-3], "") + "/" + sym[-2:], "simbolo": sym,
                "nota": "" if nota == "último negócio" else nota,
            }
    return out


def coleta_comex(h):
    # dado mensal: busca uma vez por dia e guarda no histórico
    hoje = AGORA.date().isoformat()
    cache = h.get("comex") or {}
    if cache.get("dia") == hoje and (cache.get("serie") or {}).get("soja"):
        return cache["serie"]
    # o Comex Stat aplica o intervalo de meses a cada ano: "to" vai sempre até dezembro
    # o Comex Stat bloqueia por excesso de consulta: depois de uma falha, espera 1 h
    falha = cache.get("falha")
    if falha and AGORA - datetime.datetime.fromisoformat(falha) < datetime.timedelta(hours=1):
        raise RuntimeError("aguardando liberação do Comex Stat")
    try:
        serie = busca_comex()
    except Exception:
        cache["falha"] = AGORA.isoformat(timespec="seconds")
        h["comex"] = cache
        raise
    try:
        cache["ptax_mes"] = busca_ptax_mensal()
    except Exception:
        pass
    cache.update({"dia": hoje, "serie": serie, "falha": None})
    h["comex"] = cache
    return serie


def busca_ptax_mensal():
    # média mensal da PTAX de venda, para converter o histórico em R$/t
    url = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoDolarPeriodo("
           "dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)?@dataInicial=%%27%s%%27"
           "&@dataFinalCotacao=%%27%s%%27&%%24format=json&%%24select=cotacaoVenda,dataHoraCotacao"
           % (COMEX_INICIO[5:] + "-01-" + COMEX_INICIO[:4], AGORA.strftime("%m-%d-%Y")))
    soma = {}
    for v in get_json(url)["value"]:
        m = v["dataHoraCotacao"][:7]
        a = soma.setdefault(m, [0.0, 0])
        a[0] += v["cotacaoVenda"]
        a[1] += 1
    return {m: a[0] / a[1] for m, a in soma.items()}


def comex_consulta(fluxo, ncms, metricas):
    corpo = json.dumps({"flow": fluxo, "monthDetail": True,
                        "period": {"from": COMEX_INICIO, "to": AGORA.strftime("%Y") + "-12"},
                        "filters": [{"filter": "ncm", "values": ncms}], "details": ["ncm"],
                        "metrics": metricas}).encode()
    req = urllib.request.Request("https://api-comexstat.mdic.gov.br/general?language=pt", data=corpo,
                                 headers=dict(UA, **{"Content-Type": "application/json"}))
    with urllib.request.urlopen(req, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))["data"]["list"]


def busca_comex():
    lista = comex_consulta("import", list(COMEX_NCM), ["metricFOB", "metricKG", "metricFreight"])
    serie = {}
    for r in lista:
        t = float(r["metricKG"]) / 1000
        if t < 1000:   # mês com volume irrisório distorce a média
            continue
        serie.setdefault(COMEX_NCM[r["coNcm"]][0], []).append({
            "mes": "%s-%s" % (r["year"], r["monthNumber"]), "fob": float(r["metricFOB"]) / t,
            "frete": float(r["metricFreight"]) / t, "kt": t / 1000})
    # soja em grão exportada: preço FOB porto, base da relação de troca
    for r in comex_consulta("export", [NCM_SOJA], ["metricFOB", "metricKG"]):
        t = float(r["metricKG"]) / 1000
        if t >= 1000:
            serie.setdefault("soja", []).append({"mes": "%s-%s" % (r["year"], r["monthNumber"]),
                                                 "fob": float(r["metricFOB"]) / t, "kt": t / 1000})
    for v in serie.values():
        v.sort(key=lambda x: x["mes"])
    return serie


def coleta_ptax():
    ini = (AGORA - datetime.timedelta(days=10)).strftime("%m-%d-%Y")
    fim = AGORA.strftime("%m-%d-%Y")
    url = ("https://olinda.bcb.gov.br/olinda/servico/PTAX/versao/v1/odata/CotacaoMoedaPeriodo("
           "moeda=@moeda,dataInicial=@dataInicial,dataFinalCotacao=@dataFinalCotacao)"
           "?@moeda='USD'&@dataInicial='%s'&@dataFinalCotacao='%s'&$format=json" % (ini, fim))
    vals = get_json(url.replace("'", "%27").replace("$", "%24"))["value"]
    if not vals:
        return {}
    ult = vals[-1]
    dia_ult = ult["dataHoraCotacao"][:10]
    fech_ant = [v for v in vals if v["tipoBoletim"] == "Fechamento" and v["dataHoraCotacao"][:10] < dia_ult]
    hora = datetime.datetime.strptime(ult["dataHoraCotacao"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=TZ)
    bol = {"Abertura": "boletim de abertura", "Intermediário": "boletim intermediário",
           "Fechamento": "fechamento"}.get(ult["tipoBoletim"], ult["tipoBoletim"])
    return {"ptax": {"valor": ult["cotacaoVenda"], "ant": fech_ant[-1]["cotacaoVenda"] if fech_ant else None,
                     "min": None, "max": None, "hora": hora, "fonte": "Banco Central",
                     "contrato": "venda", "nota": bol}}


# ---------------------------------------------------------------- HISTÓRICO (sparkline do dia)
def carrega_hist():
    try:
        with open(HIST, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"serie": {}, "ultimo": {}}


def salva_hist(h, dados, erros):
    hoje = AGORA.date().isoformat()
    for k, d in dados.items():
        if d.get("valor") is None or d.get("antigo"):
            continue
        serie = [p for p in h["serie"].get(k, []) if p[0][:10] >= (AGORA - datetime.timedelta(days=1)).date().isoformat()]
        serie.append([AGORA.isoformat(timespec="minutes"), d["valor"]])
        h["serie"][k] = serie[-500:]
        u = dict(d)
        u["hora"] = d["hora"].isoformat() if d.get("hora") else None
        h["ultimo"][k] = u
    h["erros"] = erros
    h["gerado"] = AGORA.isoformat(timespec="seconds")
    os.makedirs(OUT_DIR, exist_ok=True)
    tmp = HIST + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(h, f, ensure_ascii=False)
    os.replace(tmp, HIST)
    return hoje


# ---------------------------------------------------------------- FORMATAÇÃO
def br(v, d=2):
    if v is None:
        return "—"
    s = "{:,.{}f}".format(v, d)
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def var(d):
    if d.get("valor") is None or not d.get("ant"):
        return None, None
    return d["valor"] - d["ant"], (d["valor"] / d["ant"] - 1) * 100


def classe(p):
    if p is None or abs(p) < 0.005:
        return "flat"
    return "up" if p > 0 else "down"


def seta(p):
    return {"up": "▲", "down": "▼"}.get(classe(p), "■")


def hora_txt(d):
    h = d.get("hora")
    if not h:
        return ""
    if h.date() == AGORA.date():
        return h.strftime("%H:%M")
    return h.strftime("%d/%m")


def sparkline(serie, w=120, h=28, pts=None):
    hoje = AGORA.date().isoformat()
    if pts is None:
        pts = [p[1] for p in serie if p[0][:10] == hoje]
    if len(pts) < 3:
        return ""
    lo, hi = min(pts), max(pts)
    rng = (hi - lo) or 1
    n = len(pts) - 1
    xy = ["%.1f,%.1f" % (i * w / n, h - 3 - (v - lo) / rng * (h - 6)) for i, v in enumerate(pts)]
    ex, ey = xy[-1].split(",")
    return ('<svg class="spark" viewBox="0 0 %d %d" preserveAspectRatio="none" aria-hidden="true">'
            '<polyline points="%s"/><circle cx="%s" cy="%s" r="2.2"/></svg>' % (w, h, " ".join(xy), ex, ey))


def faixa(d):
    lo, hi, v, ant = d.get("min"), d.get("max"), d.get("valor"), d.get("ant")
    if not lo or not hi or v is None or hi <= lo:
        return ""
    vals = [lo, hi, v] + ([ant] if ant else [])
    a, b = min(vals), max(vals)
    pos = lambda x: (x - a) / (b - a) * 100
    marca_ant = '<i class="prev" style="left:%.1f%%"></i>' % pos(ant) if ant else ""
    return ('<div class="range" title="mínima e máxima do dia"><span class="lo">%s</span><div class="bar">'
            '<b style="left:%.1f%%;width:%.1f%%"></b>%s<i class="now" style="left:%.1f%%"></i></div>'
            '<span class="hi">%s</span></div>' % (br(lo, d.get("dec", 2)), pos(lo), pos(hi) - pos(lo),
                                                   marca_ant, pos(v), br(hi, d.get("dec", 2))))


def tile(d, nome, dec=2, pre="", suf="", sub="", big=False, hist=None, chave=None, pp=False, pts=None):
    if not d:
        return ('<div class="tile flat%s"><div class="t-head"><span class="t-name">%s</span></div>'
                '<div class="t-val">—</div><div class="t-foot">sem dado</div></div>' % (" big" if big else "", nome))
    dv, dp = var(d)
    d["dec"] = dec
    if dp is None:
        chg = "sem referência anterior"
    elif pp:
        chg = "%s %s%s p.p." % (seta(dp), "+" if dv > 0 else "", br(dv, dec))
    else:
        chg = "%s %s%s · %s%s%%" % (seta(dp), "+" if dv > 0 else "", br(dv, dec), "+" if dp > 0 else "", br(dp, 2))
    nota = d.get("nota") or ""
    if d.get("antigo"):
        nota = "dado antigo" + (" · " + nota if nota else "")
    tag = '<span class="tag">%s</span>' % html.escape(nota) if nota else ""
    ctr = '<span class="t-ctr">%s</span>' % html.escape(d.get("contrato", "")) if d.get("contrato") else ""
    graf = sparkline([], pts=pts) if pts else (sparkline((hist or {}).get(chave, [])) if chave else "")
    return ('<div class="tile %s%s"><div class="t-head"><span class="t-name">%s</span>%s</div>'
            '<div class="t-val">%s%s<small>%s</small></div><div class="t-chg">%s</div>%s%s%s'
            '<div class="t-foot">%s · %s%s</div></div>'
            % (classe(dp), " big" if big else "", nome, ctr, pre, br(d.get("valor"), dec), suf, chg,
               '<div class="t-sub">%s</div>' % sub if sub else "", faixa(d), graf,
               d.get("fonte", ""), hora_txt(d), tag))


def cel_var(d):
    dv, dp = var(d) if d else (None, None)
    if dp is None:
        return '<td class="num flat">—</td>'
    return '<td class="num %s">%s %s%s%%</td>' % (classe(dp), seta(dp), "+" if dp > 0 else "", br(dp, 2))


# ---------------------------------------------------------------- CURVA DOS FERTILIZANTES
def serie_fert(comex, k, ptax_mes=None):
    pts = []
    for x in comex.get(k, []):
        v = x["fob"] + x["frete"]
        if ptax_mes is not None:
            c = ptax_mes.get(x["mes"])
            if not c:
                continue
            v *= c
        pts.append((x["mes"], v))
    return pts


def mes_txt(m):
    return "%s/%s" % (list(MES_B3.values())[int(m[5:]) - 1], m[2:4])


def grafico_fert(comex, ptax_mes, unidade, ultimos=None, linhas=None, dec=0):
    # ultimos=13 dá a janela de 12 meses (mesmo mês do ano anterior até o último)
    W, H, L, R, T, B = 900, 340, 62, 110, 22, 30
    if linhas is None:
        linhas = {k: serie_fert(comex, k, ptax_mes) for k, _ in COMEX_NCM.values()}
    tem = sorted({m for p in linhas.values() for m, _ in p})
    meses = []
    if tem:  # eixo no calendário: mês sem dado vira buraco na linha, não some do eixo
        y, mm = int(tem[0][:4]), int(tem[0][5:])
        while "%d-%02d" % (y, mm) <= tem[-1]:
            meses.append("%d-%02d" % (y, mm))
            y, mm = (y + 1, 1) if mm == 12 else (y, mm + 1)
    if ultimos:
        meses = meses[-ultimos:]
        linhas = {k: [x for x in p if x[0] >= meses[0]] for k, p in linhas.items()}
    if len(meses) < 2:
        return '<p class="vazio">Histórico ainda não carregado. O Comex Stat entra numa das próximas atualizações.</p>'
    idx = {m: i for i, m in enumerate(meses)}
    vmax = max(v for p in linhas.values() for _, v in p)
    passo = next(p for p in (2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000) if vmax / p <= 7)
    topo = -(-vmax // passo) * passo
    X = lambda i: L + i * (W - L - R) / (len(meses) - 1)
    Y = lambda v: T + (H - T - B) * (1 - v / topo)
    o = ['<svg class="fchart" viewBox="0 0 %d %d" role="img" aria-label="Evolução do preço CFR de ureia, KCl e MAP">' % (W, H)]
    v = 0
    while v <= topo:
        o.append('<line class="gl" x1="%d" x2="%d" y1="%.1f" y2="%.1f"/><text class="ax" x="%d" y="%.1f" text-anchor="end">%s</text>'
                 % (L, W - R, Y(v), Y(v), L - 8, Y(v) + 4, br(v, 0)))
        v += passo
    for m in meses:
        if len(meses) <= 18:   # janela curta: um rótulo por mês
            x = X(idx[m])
            o.append('<line class="gl yr" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/><text class="ax" x="%.1f" y="%d" text-anchor="middle">%s</text>'
                     % (x, x, T, H - B, x, H - 10, mes_txt(m)))
        elif m.endswith("-01"):
            x = X(idx[m])
            o.append('<line class="gl yr" x1="%.1f" x2="%.1f" y1="%d" y2="%d"/><text class="ax" x="%.1f" y="%d">%s</text>'
                     % (x, x, T, H - B, x + 4, H - 10, m[:4]))
    o.append('<text class="ax un" x="%d" y="%d">%s</text>' % (L, T - 8, unidade))
    rotulos = []
    for k, nome in COMEX_NCM.values():
        p = linhas[k]
        if not p:
            continue
        trechos, atual = [], []
        for m, v in p:
            if atual and idx[m] != idx[atual[-1][0]] + 1:
                trechos.append(atual)
                atual = []
            atual.append((m, v))
        trechos.append(atual)
        for t in trechos:
            if len(t) > 1:
                o.append('<polyline class="ln %s" points="%s"/>'
                         % (k, " ".join("%.1f,%.1f" % (X(idx[m]), Y(v)) for m, v in t)))
        for m, v in p:
            o.append('<circle class="hit" cx="%.1f" cy="%.1f" r="5"><title>%s %s: %s %s</title></circle>'
                     % (X(idx[m]), Y(v), nome, mes_txt(m), br(v, dec), unidade))
        m, v = p[-1]
        o.append('<circle class="dot %s" cx="%.1f" cy="%.1f" r="3.5"/>' % (k, X(idx[m]), Y(v)))
        rotulos.append([Y(v), k, nome, v])
    rotulos.sort()
    for i in range(1, len(rotulos)):  # afasta rótulos que se sobrepõem
        rotulos[i][0] = max(rotulos[i][0], rotulos[i - 1][0] + 36)
    for y, k, nome, v in rotulos:
        o.append('<text class="lb %s" x="%d" y="%.1f">%s</text><text class="lv" x="%d" y="%.1f">%s</text>'
                 % (k, W - R + 12, y, nome, W - R + 12, y + 17, br(v, dec)))
    o.append("</svg>")
    return "".join(o)


def serie_troca(comex):
    # sacas de soja (60 kg, FOB exportação) para pagar 1 t de fertilizante (CFR importação)
    soja = {x["mes"]: x["fob"] * 0.06 for x in comex.get("soja", [])}
    return {k: [(m, v / soja[m]) for m, v in serie_fert(comex, k) if soja.get(m)]
            for k, _ in COMEX_NCM.values()}


def tabela_troca(comex, soja_hoje):
    linhas = []
    tr = serie_troca(comex)
    for k, nome in COMEX_NCM.values():
        p = tr.get(k) or []
        if not p:
            continue
        m, v = p[-1]
        mp = dict(p)
        ano = mp.get("%d-%s" % (int(m[:4]) - 1, m[5:]))
        media = sum(x for _, x in p) / len(p)
        melhor = min(p, key=lambda x: x[1])
        pior = max(p, key=lambda x: x[1])
        dm = (v / media - 1) * 100
        hoje = ""
        fert = serie_fert(comex, k)
        if soja_hoje and fert:
            hoje = br(fert[-1][1] / soja_hoje, 1)
        linhas.append(
            "<tr><th>%s</th><td class='num v'>%s sc<small>%s</small></td><td class='num'>%s</td>"
            "<td class='num'>%s</td><td class='num %s'>%s%s%%</td>"
            "<td class='num'>%s<small>%s</small></td><td class='num'>%s<small>%s</small></td>"
            "<td class='num brl'>%s</td></tr>"
            % (nome, br(v, 1), mes_txt(m), br(ano, 1) if ano else "—", br(media, 1),
               "down" if dm > 0.5 else ("up" if dm < -0.5 else "flat"), "+" if dm > 0 else "", br(dm, 0),
               br(melhor[1], 1), mes_txt(melhor[0]), br(pior[1], 1), mes_txt(pior[0]),
               (hoje + " sc") if hoje else "—"))
    return "".join(linhas)


def tabela_fert(comex, spot):
    linhas = []
    for k, nome in COMEX_NCM.values():
        p = serie_fert(comex, k)
        if not p:
            continue
        m, v = p[-1]
        mp = dict(p)

        def delta(meses_atras):
            y, mm = int(m[:4]), int(m[5:]) - meses_atras
            while mm <= 0:
                y, mm = y - 1, mm + 12
            ref = mp.get("%d-%02d" % (y, mm))
            if not ref:
                return '<td class="num flat">—</td>'
            d = (v / ref - 1) * 100
            return '<td class="num %s">%s%s%%</td>' % (classe(d), "+" if d > 0 else "", br(d, 1))
        pico = max(p, key=lambda x: x[1])
        piso = min(p, key=lambda x: x[1])
        linhas.append(
            "<tr><th>%s</th><td class='num v'>US$ %s<small>%s</small></td>%s%s%s"
            "<td class='num'>US$ %s<small>%s</small></td><td class='num down'>%s%%</td>"
            "<td class='num'>US$ %s<small>%s</small></td><td class='num brl'>%s</td></tr>"
            % (nome, br(v, 0), mes_txt(m), delta(1), delta(3), delta(12), br(pico[1], 0), mes_txt(pico[0]),
               br((v / pico[1] - 1) * 100, 0), br(piso[1], 0), mes_txt(piso[0]),
               ("R$ " + br(v * spot, 0)) if spot else "—"))
    return "".join(linhas)


# ---------------------------------------------------------------- MONTAGEM
def main():
    h = carrega_hist()
    dados, erros = {}, []
    for nome, fn in (("CNBC", coleta_cnbc), ("B3", coleta_b3), ("Banco Central", coleta_ptax)):
        try:
            dados.update(fn())
        except Exception as e:  # fonte fora do ar não derruba o painel
            erros.append("%s: %s" % (nome, str(e)[:120]))
    # o que faltou nesta rodada vem do último valor guardado, marcado como antigo
    for k, u in h.get("ultimo", {}).items():
        if k not in dados:
            d = dict(u)
            d["hora"] = datetime.datetime.fromisoformat(u["hora"]) if u.get("hora") else None
            d["antigo"] = True
            dados[k] = d
    try:
        comex = coleta_comex(h)
    except Exception as e:
        comex = (h.get("comex") or {}).get("serie") or {}
        erros.append("Comex Stat: %s" % str(e)[:120])
    salva_hist(h, dados, erros)
    hist = h["serie"]
    g = dados.get

    # ureia Golfo EUA: US$/short ton -> US$/t
    if g("urea"):
        for c in ("valor", "ant", "min", "max"):
            if dados["urea"].get(c):
                dados["urea"][c] = dados["urea"][c] / ST_T

    # dólar B3 vem em R$ por US$ 1.000
    for k in ("DOL_1", "DOL_27"):
        if g(k):
            for c in ("valor", "ant", "min", "max"):
                if dados[k].get(c):
                    dados[k][c] = dados[k][c] / 1000

    d1, d27 = g("DOL_1"), g("DOL_27")
    if d1 and d27 and d27.get("valor") is None and d1.get("valor") and d1.get("ant") and d27.get("ant"):
        d27["valor"] = d1["valor"] * d27["ant"] / d1["ant"]
        d27["nota"] = "sem negócio — estimado pela curva"

    # conversões para unidade de negócio
    def conv(d, fator, cambio=None):
        if not d or d.get("valor") is None:
            return None
        v = d["valor"] * fator
        return v * cambio if cambio else v

    dol27 = (g("DOL_27") or {}).get("valor") or (g("usdbrl") or {}).get("valor")
    spot = (g("usdbrl") or {}).get("valor")

    def linha_comm(nome, und, d1, d27, fator, unidade_conv, conv_brl=True):
        u1 = conv(d1, fator)
        u27 = conv(d27, fator)
        r27 = (u27 * dol27 if (u27 and dol27) else None) if conv_brl else u27
        return ("<tr><th>%s<small>%s</small></th>"
                "<td class='ctr'>%s</td><td class='num v'>%s</td>%s<td class='num conv'>%s</td>"
                "<td class='ctr sep'>%s</td><td class='num v'>%s</td>%s<td class='num conv'>%s</td>"
                "<td class='num brl'>%s</td></tr>"
                % (nome, und, (d1 or {}).get("contrato", "—"), br((d1 or {}).get("valor"), 2), cel_var(d1),
                   (unidade_conv % br(u1, 2)) if u1 else "", (d27 or {}).get("contrato", "—"),
                   br((d27 or {}).get("valor"), 2), cel_var(d27),
                   (unidade_conv % br(u27, 2)) if u27 else "", ("R$ " + br(r27, 2)) if r27 else "—"))

    tabela = "".join([
        linha_comm("Soja CBOT", "¢/bushel", g("S_1"), g("S_27"), BU_SC_SOJA / 100, "US$ %s/sc"),
        linha_comm("Soja B3", "US$/sc 60 kg", g("SJC_1"), g("SJC_27"), 1, "US$ %s/sc"),
        linha_comm("Milho CBOT", "¢/bushel", g("C_1"), g("C_27"), BU_SC_MILHO / 100, "US$ %s/sc"),
        linha_comm("Milho B3", "R$/sc 60 kg", g("CCM_1"), g("CCM_27"), 1, "R$ %s/sc", conv_brl=False),
        linha_comm("Algodão ICE", "¢/libra-peso", g("CT_1"), g("CT_27"), LB_ARROBA / 100, "US$ %s/@"),
    ])

    # ---- leitura automática para negociação de insumos
    leitura = []
    if g("usdbrl") and var(g("usdbrl"))[1] is not None:
        dv, dp = var(g("usdbrl"))
        leitura.append(
            "<b>Câmbio:</b> dólar a R$ %s (%s%s%% sobre o fechamento anterior). US$ 1 milhão em defensivo ou "
            "fertilizante custa hoje <b>R$ %s</b>, R$ %s %s que no fechamento anterior."
            % (br(spot, 4), "+" if dp > 0 else "", br(dp, 2), br(spot * 1e6, 0), br(abs(dv) * 1e6, 0),
               "a mais" if dv > 0 else "a menos"))
    if g("DOL_27") and g("DOL_27").get("valor") and spot:
        fw = g("DOL_27")["valor"]
        leitura.append(
            "<b>Dólar para o vencimento de safra:</b> o futuro %s está em R$ %s, %s%% acima do spot. "
            "É o custo de travar hoje o câmbio de uma compra com pagamento em %s."
            % (g("DOL_27")["contrato"], br(fw, 4), br((fw / spot - 1) * 100, 2), g("DOL_27")["contrato"]))
    if g("DI1F28") and var(g("DI1F28"))[0] is not None:
        dv, _ = var(g("DI1F28"))
        leitura.append(
            "<b>Juros:</b> DI jan/28 a %s%% a.a. (%s%s p.p.). Juro maior encarece o prazo — "
            "confira a taxa embutida em toda proposta a prazo contra o preço à vista."
            % (br(g("DI1F28")["valor"], 3), "+" if dv > 0 else "", br(dv, 3)))
    if g("S_27") and g("S_27").get("valor") and dol27:
        usc = g("S_27")["valor"] * BU_SC_SOJA / 100
        leitura.append(
            "<b>Poder de compra em soja:</b> soja %s a US$ %s/sc na CBOT, R$ %s/sc ao dólar %s "
            "(paridade de bolsa, sem prêmio de porto nem frete). Barter cotado em soja se protege do câmbio; "
            "pagamento em reais não."
            % (g("S_27")["contrato"], br(usc, 2), br(usc * dol27, 2), (g("DOL_27") or {}).get("contrato", "spot")))
    if g("brent") and var(g("brent"))[1] is not None:
        ng = g("ng")
        ng_txt = ""
        if ng and var(ng)[1] is not None:
            ng_txt = "; gás natural (Henry Hub) %s%s%%, insumo da ureia" % ("+" if var(ng)[1] > 0 else "", br(var(ng)[1], 2))
        leitura.append(
            "<b>Energia:</b> Brent a US$ %s (%s%s%%)%s. Petróleo pressiona diesel, frete e nitrogenados."
            % (br(g("brent")["valor"], 2), "+" if var(g("brent"))[1] > 0 else "", br(var(g("brent"))[1], 2), ng_txt))
    if g("cny") and g("cny").get("valor"):
        dp = var(g("cny"))[1]
        leitura.append(
            "<b>China:</b> yuan a %s por dólar%s. Grande parte do ativo técnico dos defensivos vem da China; "
            "yuan mais forte encarece a origem."
            % (br(g("cny")["valor"], 4), (" (%s%s%%)" % ("+" if dp > 0 else "", br(dp, 2))) if dp is not None else ""))

    # ---- fertilizante no porto (Comex Stat)
    soja_hoje = (g("SJC_1") or {}).get("valor")   # soja B3, US$/sc — referência "hoje" da troca
    tr = serie_troca(comex)
    if any(tr.values()):
        partes = []
        for k, nome in COMEX_NCM.values():
            p = tr.get(k) or []
            if p:
                media = sum(x for _, x in p) / len(p)
                partes.append("%s %s sc/t (média desde %s: %s)" % (nome, br(p[-1][1], 1), COMEX_INICIO[:4], br(media, 1)))
        ult = next(p[-1][0] for p in tr.values() if p)
        leitura.append("<b>Relação de troca em %s:</b> %s. Acima da média, o fertilizante está caro em soja."
                       % (mes_txt(ult), "; ".join(partes)))
    fert_tiles, fert_brl = [], []
    for _, (k, nome) in COMEX_NCM.items():
        sr = comex.get(k) or []
        if not sr:
            fert_tiles.append(tile(None, nome + " CFR"))
            continue
        u = sr[-1]
        cfr = u["fob"] + u["frete"]
        d = {"valor": cfr, "ant": (sr[-2]["fob"] + sr[-2]["frete"]) if len(sr) > 1 else None,
             "contrato": mes_txt(u["mes"]), "fonte": "Comex Stat", "hora": None, "nota": "mensal · var. vs mês ant."}
        sub = "FOB %s + frete %s · %s mil t" % (br(u["fob"], 0), br(u["frete"], 0), br(u["kt"], 0))
        tk = serie_troca(comex).get(k) or []
        if tk and tk[-1][0] == u["mes"]:
            sub += "<br><b>%s sacas de soja</b> por tonelada" % br(tk[-1][1], 1)
        if spot:
            sub += "<br><b>R$ %s/t</b> ao dólar de hoje" % br(cfr * spot, 0)
            fert_brl.append("%s R$ %s/t" % (nome.split(" ")[0], br(cfr * spot, 0)))
        fert_tiles.append(tile(d, nome + " CFR", 0, "US$ ", "/t", sub=sub,
                               pts=[x["fob"] + x["frete"] for x in sr[-6:]]))
    fert_tiles.append(tile(g("urea"), "Ureia Golfo EUA", 0, "US$ ", "/t",
                           sub="futuro CME, sinal diário — pouco negócio", hist=hist, chave="urea"))
    if fert_brl:
        ult = (comex.get("ureia") or comex.get("kcl") or [{}])[-1].get("mes")
        leitura.append(
            "<b>Fertilizante no porto:</b> média CFR de importação de %s convertida ao dólar de hoje — %s. "
            "É preço de porto: falta internação, frete interno e margem da revenda. Dado mensal, não mostra o choque de hoje."
            % (mes_txt(ult) if ult else "último mês", "; ".join(fert_brl)))

    falhas = [e for e in erros if "aguardando" not in e]   # espera programada do Comex Stat não é falha
    status = ('<span class="ok">fontes ok</span>' if not falhas else
              '<span class="warn">falha: %s</span>' % html.escape("; ".join(e.split(":")[0] for e in falhas)))

    ewz_sub = ""
    if g("ewz") and g("ewz").get("nota"):
        ewz_sub = "fechamento anterior US$ %s" % br(g("ewz").get("ant"), 2)

    corpo = TEMPLATE.format(
        data=AGORA.strftime("%d/%m/%Y"), hora=AGORA.strftime("%H:%M"),
        proxima=(AGORA + datetime.timedelta(minutes=2)).strftime("%H:%M"), status=status,
        hero="".join([
            tile(g("usdbrl"), "Dólar comercial", 4, "R$ ", big=True, hist=hist, chave="usdbrl"),
            tile(g("DOL_1"), "Dólar futuro B3", 4, "R$ ", big=True, hist=hist, chave="DOL_1"),
            tile(g("ibov"), "Ibovespa", 0, "", " pts", big=True, hist=hist, chave="ibov"),
            tile(g("ewz"), "EWZ — Brasil em NY", 2, "US$ ", sub=ewz_sub, big=True, hist=hist, chave="ewz"),
        ]),
        tabela=tabela,
        cambio="".join([
            tile(g("ptax"), "PTAX", 4, "R$ ", hist=hist, chave="ptax"),
            tile(g("DOL_27"), "Dólar futuro safra", 4, "R$ ", hist=hist, chave="DOL_27"),
            tile(g("DI1F27"), "DI jan/27", 3, "", "%", hist=hist, chave="DI1F27", pp=True),
            tile(g("DI1F28"), "DI jan/28", 3, "", "%", hist=hist, chave="DI1F28", pp=True),
            tile(g("dxy"), "DXY — dólar global", 2, hist=hist, chave="dxy"),
            tile(g("cny"), "Yuan por dólar", 4, hist=hist, chave="cny"),
        ]),
        energia="".join([
            tile(g("brent"), "Petróleo Brent", 2, "US$ ", hist=hist, chave="brent"),
            tile(g("wti"), "Petróleo WTI", 2, "US$ ", hist=hist, chave="wti"),
            tile(g("ng"), "Gás natural", 3, "US$ ", "/MMBtu", hist=hist, chave="ng"),
        ]),
        fert="".join(fert_tiles),
        graf_usd_12=grafico_fert(comex, None, "US$/t", 13),
        graf_brl_12=grafico_fert(comex, (h.get("comex") or {}).get("ptax_mes") or {}, "R$/t", 13),
        graf_usd=grafico_fert(comex, None, "US$/t"),
        graf_brl=grafico_fert(comex, (h.get("comex") or {}).get("ptax_mes") or {}, "R$/t"),
        tab_fert=tabela_fert(comex, spot),
        graf_tr_12=grafico_fert(comex, None, "sc/t", 13, linhas=serie_troca(comex), dec=1),
        graf_tr=grafico_fert(comex, None, "sc/t", linhas=serie_troca(comex), dec=1),
        tab_troca=tabela_troca(comex, soja_hoje),
        ini_fert=mes_txt(COMEX_INICIO),
        global_="".join([
            tile(g("IND_1"), "Ibovespa futuro", 0, "", " pts", hist=hist, chave="IND_1"),
            tile(g("spx"), "S&P 500", 2, hist=hist, chave="spx"),
            tile(g("vix"), "VIX — medo global", 2, hist=hist, chave="vix"),
            tile(g("us10"), "Treasury 10 anos", 3, "", "%", hist=hist, chave="us10", pp=True),
        ]),
        leitura="".join("<li>%s</li>" % l for l in leitura),
    )
    tmp = OUT_HTML + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(corpo)
    os.replace(tmp, OUT_HTML)

    # ---- widget: versão compacta para a janela fixa (app PainelWidget)
    def wl(nome, d, dec=2, pre="", suf="", pp=False, sub=""):
        if not d or d.get("valor") is None:
            return '<div class="r"><span class="n">%s</span><span class="v">—</span><span class="c flat"></span></div>' % nome
        dv, dp = var(d)
        if dp is None:
            c, cl = "", "flat"
        elif pp:
            c, cl = "%s%s p.p." % ("+" if dv > 0 else "", br(dv, dec)), classe(dp)
        else:
            c, cl = "%s%s%%" % ("+" if dp > 0 else "", br(dp, 2)), classe(dp)
        ctr = ' <i>%s</i>' % d["contrato"] if d.get("contrato") else ""
        sub = ' <i>%s</i>' % sub if sub else ""
        return ('<div class="r"><span class="n">%s%s%s</span><span class="v">%s%s%s</span><span class="c %s">%s</span></div>'
                % (nome, ctr, sub, pre, br(d["valor"], dec), suf, cl, c))

    def sc(d, fator):
        if not d or d.get("valor") is None:
            return None
        e = dict(d)
        for c in ("valor", "ant"):
            if e.get(c):
                e[c] = e[c] * fator
        return e

    fert_w, troca_w = [], []
    trw = serie_troca(comex)
    for k, nome in COMEX_NCM.values():
        sr = comex.get(k) or []
        if sr:
            u, a = sr[-1], (sr[-2] if len(sr) > 1 else None)
            d = {"valor": u["fob"] + u["frete"], "ant": (a["fob"] + a["frete"]) if a else None,
                 "contrato": mes_txt(u["mes"])}
            t = trw.get(k) or []
            fert_w.append(wl(nome.split(" ")[0], d, 0, "US$ ", "/t"))
            if t:
                ant = t[-2][1] if len(t) > 1 and t[-2][0] < t[-1][0] else None
                troca_w.append(wl(nome.split(" ")[0], {"valor": t[-1][1], "ant": ant, "contrato": mes_txt(t[-1][0])},
                                  1, "", " sc"))
    widget = WIDGET.format(
        hora=AGORA.strftime("%H:%M"), ok="ok" if not falhas else "warn",
        cambio="".join([wl("Dólar", g("usdbrl"), 4, "R$ "), wl("Dólar fut.", g("DOL_1"), 4, "R$ "),
                        wl("Dólar safra", g("DOL_27"), 4, "R$ "), wl("DI", g("DI1F28"), 2, "", "%", pp=True)]),
        bolsa="".join([wl("Ibovespa", g("ibov"), 0), wl("Ibov fut.", g("IND_1"), 0), wl("EWZ", g("ewz"), 2, "US$ ")]),
        graos="".join([wl("Soja CBOT", sc(g("S_27"), BU_SC_SOJA / 100), 2, "US$ ", "/sc"),
                       wl("Soja B3", g("SJC_27"), 2, "US$ ", "/sc"),
                       wl("Milho B3", g("CCM_27"), 2, "R$ ", "/sc"),
                       wl("Algodão", g("CT_1"), 2, "", " ¢/lb"),
                       wl("Algodão", g("CT_27"), 2, "", " ¢/lb")]),
        energia="".join([wl("Brent", g("brent"), 2, "US$ "), wl("Gás natural", g("ng"), 2, "US$ ")]),
        fert="".join(fert_w) or '<div class="r"><span class="n">aguardando Comex Stat</span></div>',
        troca="".join(troca_w) or '<div class="r"><span class="n">aguardando Comex Stat</span></div>',
    )
    # ---- dados.json: números prontos para o widget do iPhone (Scriptable), em ordem de prioridade
    def item(nome, d, dec=2, pre="", suf="", pp=False):
        if not d or d.get("valor") is None:
            return None
        dv, dp = var(d)
        if dp is None:
            vt, cl = "", "flat"
        elif pp:
            vt, cl = "%s%s p.p." % ("+" if dv > 0 else "", br(dv, dec)), classe(dp)
        else:
            vt, cl = "%s%s%%" % ("+" if dp > 0 else "", br(dp, 2)), classe(dp)
        return {"nome": nome, "ctr": d.get("contrato", ""), "valor": pre + br(d["valor"], dec) + suf,
                "var": vt, "dir": cl}
    def troca(k, nome):
        t = trw.get(k) or []
        if not t:
            return None
        return item("Troca " + nome, {"valor": t[-1][1], "ant": t[-2][1] if len(t) > 1 else None,
                                      "contrato": mes_txt(t[-1][0])}, 1, "", " sc")
    itens = [item("Dólar", g("usdbrl"), 4, "R$ "), item("Dólar safra", g("DOL_27"), 4, "R$ "),
             item("Soja B3", g("SJC_27"), 2, "US$ "), item("Milho B3", g("CCM_27"), 2, "R$ "),
             item("Algodão", g("CT_27"), 2, "", "¢"), troca("ureia", "ureia"),
             item("Dólar fut.", g("DOL_1"), 4, "R$ "), item("DI", g("DI1F28"), 2, "", "%", pp=True),
             item("Ibovespa", g("ibov"), 0), item("EWZ", g("ewz"), 2, "US$ "),
             item("Soja CBOT", sc(g("S_27"), BU_SC_SOJA / 100), 2, "US$ "),
             item("Algodão", g("CT_1"), 2, "", "¢"), item("Brent", g("brent"), 2, "US$ "),
             troca("kcl", "KCl"), troca("map", "MAP")]
    with open(os.path.join(OUT_DIR, "dados.json"), "w", encoding="utf-8") as f:
        json.dump({"hora": AGORA.strftime("%H:%M"), "data": AGORA.strftime("%d/%m"), "ok": not falhas,
                   "itens": [i for i in itens if i]}, f, ensure_ascii=False)

    tmp = os.path.join(OUT_DIR, "widget.html.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(widget)
    os.replace(tmp, os.path.join(OUT_DIR, "widget.html"))
    print(AGORA.isoformat(timespec="seconds"), "ok" if not erros else erros)


TEMPLATE = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>Painel de Mercado Agro</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&display=swap">
<style>
/* Layout: faixa de manchete (4 números do dia) > quadro de commodities agora × 2027 > blocos temáticos > leitura para compras */
:root {{
  --bg:#EEF2F0; --surface:#FFFFFF; --ink:#10201B; --muted:#5A6B65; --line:#D6DFDB;
  --accent:#1E5A45; --accent-soft:#E2EEE8; --up:#0F7A45; --down:#C0332A; --flat:#6B7772;
  --up-bg:#E3F3EA; --down-bg:#FBE7E5;
  --c-ureia:#2C6CB0; --c-kcl:#C26A12; --c-map:#7A4AB5;
  --f-sans:"IBM Plex Sans",-apple-system,"Helvetica Neue",Arial,sans-serif;
  --f-mono:"IBM Plex Mono",ui-monospace,Menlo,monospace;
}}
@media (prefers-color-scheme: dark) {{ :root {{
  --bg:#0C1311; --surface:#141E1B; --ink:#E4ECE8; --muted:#8FA19A; --line:#26332F;
  --accent:#7FC4A6; --accent-soft:#18302A; --up:#3FCB83; --down:#FF6B5E; --flat:#8A9691;
  --up-bg:#123323; --down-bg:#3A1A17; --c-ureia:#6EA8E8; --c-kcl:#F0A050; --c-map:#B48CEB; color-scheme:dark; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:14px/1.4 var(--f-sans);
  padding:20px max(16px, env(safe-area-inset-right,0px)) 28px max(16px, env(safe-area-inset-left,0px)); }}
.wrap {{ max-width:1400px; margin:0 auto; display:grid; gap:16px; }}
.wrap > *, .cols > * {{ min-width:0; }}
header {{ display:flex; flex-wrap:wrap; align-items:flex-end; justify-content:space-between; gap:8px 24px;
  border-bottom:2px solid var(--ink); padding-bottom:10px; }}
h1 {{ margin:0; font-size:24px; font-weight:700; letter-spacing:-.01em; }}
h1 span {{ color:var(--accent); }}
.meta {{ font:500 13px var(--f-mono); color:var(--muted); display:flex; gap:14px; flex-wrap:wrap; }}
.meta b {{ color:var(--ink); }}
.ok {{ color:var(--up); }} .warn {{ color:var(--down); }}
h2 {{ margin:0 0 8px; font-size:12px; font-weight:600; letter-spacing:.09em; text-transform:uppercase; color:var(--muted); }}
.grid {{ display:grid; gap:10px; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); }}
.hero {{ grid-template-columns:repeat(auto-fit,minmax(240px,1fr)); }}
.tile {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; padding:12px 14px 10px;
  display:flex; flex-direction:column; gap:3px; min-width:0; border-top:3px solid var(--flat); }}
.tile.up {{ border-top-color:var(--up); }} .tile.down {{ border-top-color:var(--down); }}
.t-head {{ display:flex; justify-content:space-between; gap:8px; align-items:baseline; }}
.t-name {{ font-weight:600; font-size:13px; }}
.t-ctr {{ font:500 11px var(--f-mono); color:var(--muted); background:var(--accent-soft); padding:1px 6px; border-radius:3px; white-space:nowrap; }}
.t-val {{ font:600 22px var(--f-mono); font-variant-numeric:tabular-nums; letter-spacing:-.02em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.t-val small {{ font-size:12px; font-weight:500; color:var(--muted); margin-left:2px; }}
.big .t-val {{ font-size:32px; }}
.t-chg {{ font:600 13px var(--f-mono); font-variant-numeric:tabular-nums; }}
.up .t-chg {{ color:var(--up); }} .down .t-chg {{ color:var(--down); }} .flat .t-chg {{ color:var(--flat); }}
.big.up {{ background:linear-gradient(var(--up-bg),var(--surface) 70%); }}
.big.down {{ background:linear-gradient(var(--down-bg),var(--surface) 70%); }}
.t-sub {{ font-size:12px; color:var(--muted); }}
.t-foot {{ margin-top:auto; padding-top:4px; font:11px var(--f-mono); color:var(--muted); display:flex; gap:6px; flex-wrap:wrap; align-items:center; }}
.tag {{ background:var(--accent-soft); color:var(--accent); padding:0 5px; border-radius:3px; font-weight:600; }}
.range {{ display:flex; align-items:center; gap:6px; font:10px var(--f-mono); color:var(--muted); margin-top:4px; }}
.range .bar {{ position:relative; flex:1; height:6px; background:var(--line); border-radius:3px; }}
.range .bar b {{ position:absolute; top:0; bottom:0; background:var(--accent-soft); border:1px solid var(--accent); border-radius:3px; }}
.range .bar i {{ position:absolute; top:-3px; width:2px; height:12px; margin-left:-1px; }}
.range .bar i.now {{ background:var(--ink); }} .range .bar i.prev {{ background:var(--muted); opacity:.5; }}
.spark {{ width:100%; height:28px; margin-top:4px; }}
.spark polyline {{ fill:none; stroke:var(--accent); stroke-width:1.5; vector-effect:non-scaling-stroke; }}
.spark circle {{ fill:var(--accent); }}
.board {{ background:var(--surface); border:1px solid var(--line); border-radius:6px; overflow-x:auto; }}
table {{ width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; min-width:860px; }}
thead th {{ font-size:11px; font-weight:600; letter-spacing:.06em; text-transform:uppercase; color:var(--muted);
  text-align:right; padding:10px 12px 6px; border-bottom:1px solid var(--line); }}
thead tr.grp th {{ text-align:center; color:var(--ink); border-bottom:none; padding-bottom:0; }}
thead th:first-child {{ text-align:left; }}
tbody th {{ text-align:left; padding:10px 12px; font-size:14px; font-weight:600; white-space:nowrap; }}
tbody th small {{ display:block; font:400 11px var(--f-mono); color:var(--muted); }}
td {{ padding:10px 12px; border-top:1px solid var(--line); font-family:var(--f-mono); text-align:right; white-space:nowrap; }}
tbody th {{ border-top:1px solid var(--line); }}
tbody tr:first-child td, tbody tr:first-child th {{ border-top:none; }}
td.ctr {{ color:var(--muted); font-size:12px; text-align:center; }}
td.v {{ font-size:16px; font-weight:600; }}
td.conv {{ color:var(--muted); font-size:12px; }}
td.up {{ color:var(--up); font-weight:600; }} td.down {{ color:var(--down); font-weight:600; }} td.flat {{ color:var(--flat); }}
td.brl {{ font-size:16px; font-weight:700; color:var(--accent); background:var(--accent-soft); }}
.sep {{ border-left:2px solid var(--line); }}
.cols {{ display:grid; gap:16px; grid-template-columns:repeat(auto-fit,minmax(min(100%,560px),1fr)); }}
.read {{ background:var(--surface); border:1px solid var(--line); border-left:4px solid var(--accent); border-radius:6px; padding:14px 18px; }}
.read ul {{ margin:0; padding-left:18px; display:grid; gap:8px; }}
.read li {{ max-width:110ch; }}
.read p {{ margin:10px 0 0; font-size:11px; color:var(--muted); }}
footer {{ font:11px var(--f-mono); color:var(--muted); line-height:1.6; }}
.fert {{ background:var(--surface); border:1px solid var(--line); border-top:4px solid var(--accent); border-radius:6px;
  padding:16px 18px 18px; display:grid; gap:14px; break-before:page; }}
.fert-head p {{ margin:0; color:var(--muted); font-size:13px; }}
.fert .tile {{ background:var(--bg); }}
.fert .fgrid {{ grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); }}
.fx {{ display:flex; flex-wrap:wrap; align-items:center; gap:8px 6px; }}
.fx input {{ position:absolute; opacity:0; pointer-events:none; }}
.fx label {{ font:600 12px var(--f-mono); padding:5px 12px; border:1px solid var(--line); border-radius:4px; cursor:pointer; color:var(--muted); }}
.fx input:checked + label {{ background:var(--ink); color:var(--surface); border-color:var(--ink); }}
.fx input:focus-visible + label {{ outline:2px solid var(--accent); outline-offset:2px; }}
.legend {{ margin-left:auto; display:flex; align-items:center; gap:6px; font-size:12px; color:var(--muted); }}
.sw {{ width:14px; height:3px; border-radius:2px; display:inline-block; margin-left:8px; }}
.sw.ureia {{ background:var(--c-ureia); }} .sw.kcl {{ background:var(--c-kcl); }} .sw.map {{ background:var(--c-map); }}
.chart {{ flex-basis:100%; overflow-x:auto; }}
.chart {{ display:none; }}
#fx-usd:checked ~ #pr-12:checked ~ .c-usd.p12, #fx-brl:checked ~ #pr-12:checked ~ .c-brl.p12,
#fx-usd:checked ~ #pr-all:checked ~ .c-usd.pall, #fx-brl:checked ~ #pr-all:checked ~ .c-brl.pall {{ display:block; }}
.fx .gap {{ width:14px; }}
#tr-12:checked ~ .t12, #tr-all:checked ~ .tall {{ display:block; }}
.tr-head {{ border-top:1px solid var(--line); padding-top:14px; }}
.nota {{ margin:0; font-size:11px; color:var(--muted); }}
.fchart {{ width:100%; min-width:640px; height:auto; display:block; }}
.fchart .gl {{ stroke:var(--line); stroke-width:1; }} .fchart .gl.yr {{ stroke-dasharray:2 4; }}
.fchart .ax {{ fill:var(--muted); font:12px var(--f-mono); }} .fchart .un {{ font-weight:600; }}
.fchart .ln {{ fill:none; stroke-width:2.2; stroke-linejoin:round; }}
.fchart .ln.ureia {{ stroke:var(--c-ureia); }} .fchart .dot.ureia {{ fill:var(--c-ureia); }}
.fchart .ln.kcl {{ stroke:var(--c-kcl); }} .fchart .dot.kcl {{ fill:var(--c-kcl); }}
.fchart .ln.map {{ stroke:var(--c-map); }} .fchart .dot.map {{ fill:var(--c-map); }}
.fchart .hit {{ fill:transparent; stroke:none; }} .fchart .hit:hover {{ fill:var(--ink); }}
.fchart .lb {{ font:600 14px var(--f-sans); }} .fchart .lb.ureia {{ fill:var(--c-ureia); }}
.fchart .lb.kcl {{ fill:var(--c-kcl); }} .fchart .lb.map {{ fill:var(--c-map); }}
.fchart .lv {{ fill:var(--ink); font:600 14px var(--f-mono); }}
.ftab {{ min-width:820px; }}
.ftab td small {{ display:block; font-size:10px; color:var(--muted); font-weight:400; }}
.vazio {{ color:var(--muted); font-style:italic; margin:24px 0; }}
</style></head>
<body><div class="wrap">
<header>
  <h1>Painel de Mercado <span>Agro</span></h1>
  <div class="meta"><span><b>{data}</b> · atualizado às <b>{hora}</b></span><span>próxima ≈ {proxima}</span><span>{status}</span></div>
</header>

<section><h2>Manchete do dia</h2><div class="grid hero">{hero}</div></section>

<section><h2>Commodities — agora × safra 2027</h2>
<div class="board"><table>
<thead>
<tr class="grp"><th></th><th colspan="4">Contrato atual</th><th colspan="4" class="sep">Vencimento 2027</th><th></th></tr>
<tr><th>Produto</th><th style="text-align:center">venc.</th><th>preço</th><th>var. dia</th><th>conversão</th>
<th class="sep" style="text-align:center">venc.</th><th>preço</th><th>var. dia</th><th>conversão</th><th>2027 em R$</th></tr>
</thead>
<tbody>{tabela}</tbody></table></div>
</section>

<div class="cols">
<section><h2>Câmbio e juros</h2><div class="grid">{cambio}</div></section>
<section><h2>Energia</h2><div class="grid">{energia}</div></section>
</div>

<section><h2>Bolsa e risco</h2><div class="grid">{global_}</div></section>

<section class="fert">
  <div class="fert-head"><h2>Fertilizantes — preço no porto, CFR Brasil</h2>
  <p>Média mensal do que o Brasil pagou na importação (FOB + frete), desde {ini_fert}. Fonte Comex Stat/MDIC.</p></div>
  <div class="grid fgrid">{fert}</div>
  <div class="fx">
    <input type="radio" name="fxm" id="fx-usd" checked><label for="fx-usd">US$/t</label>
    <input type="radio" name="fxm" id="fx-brl"><label for="fx-brl">R$/t (dólar médio do mês)</label>
    <span class="gap"></span>
    <input type="radio" name="prd" id="pr-12" checked><label for="pr-12">12 meses</label>
    <input type="radio" name="prd" id="pr-all"><label for="pr-all">desde {ini_fert}</label>
    <div class="legend"><span class="sw ureia"></span>Ureia <span class="sw kcl"></span>KCl <span class="sw map"></span>MAP</div>
    <div class="chart c-usd p12">{graf_usd_12}</div>
    <div class="chart c-brl p12">{graf_brl_12}</div>
    <div class="chart c-usd pall">{graf_usd}</div>
    <div class="chart c-brl pall">{graf_brl}</div>
  </div>
  <div class="board"><table class="ftab">
  <thead><tr><th>Produto</th><th>último</th><th>vs mês ant.</th><th>3 meses</th><th>12 meses</th><th>pico</th><th>vs pico</th><th>mínima</th><th>ao dólar de hoje</th></tr></thead>
  <tbody>{tab_fert}</tbody></table></div>

  <div class="fert-head tr-head"><h2>Relação de troca — sacas de soja por tonelada</h2>
  <p>Quantas sacas de 60 kg pagam 1 t de fertilizante no porto: CFR de importação ÷ soja FOB de exportação, mês a mês.
  Linha subindo = fertilizante ficando mais caro em soja.</p></div>
  <div class="fx">
    <input type="radio" name="trp" id="tr-12" checked><label for="tr-12">12 meses</label>
    <input type="radio" name="trp" id="tr-all"><label for="tr-all">desde {ini_fert}</label>
    <div class="legend"><span class="sw ureia"></span>Ureia <span class="sw kcl"></span>KCl <span class="sw map"></span>MAP</div>
    <div class="chart t12">{graf_tr_12}</div>
    <div class="chart tall">{graf_tr}</div>
  </div>
  <div class="board"><table class="ftab">
  <thead><tr><th>Produto</th><th>último</th><th>1 ano antes</th><th>média desde {ini_fert}</th><th>vs média</th><th>melhor</th><th>pior</th><th>indicativo hoje</th></tr></thead>
  <tbody>{tab_troca}</tbody></table></div>
  <p class="nota">"Indicativo hoje" = último CFR do fertilizante ÷ soja B3 de hoje (CBOT, sem prêmio de porto); difere da relação mensal pelo prêmio de porto, que pode ser positivo ou negativo.</p>
</section>

<section class="read"><h2>Leitura para compra de insumos</h2><ul>{leitura}</ul>
<p>Texto montado automaticamente a partir dos números acima. Não substitui a análise da mesa de câmbio.</p></section>

<footer>
Fontes: B3 (dólar futuro, DI, milho e soja B3, Ibovespa futuro — atraso de 15 min) · CNBC (CBOT, ICE, petróleo, câmbio, EWZ, ações — atraso de até 10–20 min em bolsa) · Banco Central (PTAX) · Comex Stat/MDIC (fertilizante: preço médio mensal de importação, FOB + frete, sem seguro; ureia 3102.10.10, KCl 3104.20.90, MAP 3105.40.00).
Conversões: soja 2,2046 bu/sc · milho 2,3621 bu/sc · algodão 33,069 lb/@. "2027 em R$" usa o dólar futuro do vencimento de safra, sem prêmio, basis ou frete.
Variação contra o fechamento ou ajuste do dia anterior. Barra = mínima–máxima do dia (traço claro = fechamento anterior).
</footer>
</div>
<script>
["fxm", "prd", "trp"].forEach(function (g) {{
  try {{ var m = localStorage.getItem(g); if (m && document.getElementById(m)) document.getElementById(m).checked = true; }} catch (e) {{}}
  document.querySelectorAll('input[name="' + g + '"]').forEach(function (i) {{
    i.addEventListener("change", function () {{ try {{ localStorage.setItem(g, i.id); }} catch (e) {{}} }});
  }});
}});
</script>
</body></html>
"""

WIDGET = """<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="60">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-title" content="iAz">
<meta name="apple-mobile-web-app-status-bar-style" content="default">
<link rel="apple-touch-icon" href="icone-180.png">
<link rel="manifest" href="manifest.webmanifest">
<title>iAz · AzamInsights</title>
<style>
:root {{ --bg:#FFFFFF; --ink:#10201B; --muted:#5A6B65; --line:#E3E9E6; --up:#0F7A45; --down:#C0332A; --accent:#1E5A45; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#111916; --ink:#E4ECE8; --muted:#8FA19A; --line:#22302B;
  --up:#3FCB83; --down:#FF6B5E; --accent:#7FC4A6; color-scheme:dark; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:12px/1.3 -apple-system,"Helvetica Neue",sans-serif;
  padding:8px 12px 10px; -webkit-user-select:none; cursor:default; }}
header {{ display:flex; justify-content:space-between; align-items:baseline; padding-bottom:6px; border-bottom:2px solid var(--ink); }}
header b {{ font-size:13px; }} header b span {{ color:var(--accent); }} header em {{ font-style:normal; font-weight:500; font-size:10px; color:var(--muted); letter-spacing:.06em; text-transform:uppercase; margin-left:4px; }}
header small {{ font:11px ui-monospace,Menlo,monospace; color:var(--muted); }}
.dot {{ display:inline-block; width:7px; height:7px; border-radius:50%; margin-right:4px; background:var(--up); }}
.dot.warn {{ background:var(--down); }}
h3 {{ margin:8px 0 2px; font-size:10px; letter-spacing:.08em; text-transform:uppercase; color:var(--muted); font-weight:600; }}
.r {{ display:grid; grid-template-columns:1fr auto 74px; gap:6px; align-items:baseline; padding:2px 0; border-bottom:1px solid var(--line); }}
.n {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.n i {{ font-style:normal; color:var(--muted); font-size:10px; }}
.v {{ font:600 12px ui-monospace,Menlo,monospace; font-variant-numeric:tabular-nums; text-align:right; white-space:nowrap; }}
.c {{ font:600 11px ui-monospace,Menlo,monospace; text-align:right; white-space:nowrap; }}
.c.up {{ color:var(--up); }} .c.down {{ color:var(--down); }} .c.flat {{ color:var(--muted); }}
a {{ display:block; margin-top:8px; text-align:center; color:var(--accent); font-weight:600; text-decoration:none; font-size:11px; }}
html {{ -webkit-text-size-adjust:100%; }}
@media (pointer: coarse) and (max-width: 600px) {{   /* iPhone: letra maior, respeita o entalhe */
  body {{ font-size:15px; padding:calc(10px + env(safe-area-inset-top)) max(14px, env(safe-area-inset-right))
         calc(14px + env(safe-area-inset-bottom)) max(14px, env(safe-area-inset-left)); }}
  header b {{ font-size:17px; }} header small {{ font-size:13px; }}
  h3 {{ font-size:11px; margin-top:14px; }}
  .r {{ grid-template-columns:1fr auto 84px; padding:6px 0; }}
  .n i {{ font-size:12px; }} .v {{ font-size:15px; }} .c {{ font-size:13px; }}
  a {{ font-size:15px; margin-top:16px; padding:10px; }}
}}
</style></head><body>
<header><b><span>i</span>Az <em>AzamInsights · mercado agro</em></b><small><span class="dot {ok}"></span>{hora}</small></header>
<h3>Câmbio e juros</h3>{cambio}
<h3>Bolsa</h3>{bolsa}
<h3>Grãos — safra 2027</h3>{graos}
<h3>Energia</h3>{energia}
<h3>Fertilizante CFR porto — mês</h3>{fert}
<h3>Troca — sacas de soja por t</h3>{troca}
<a href="painel_mercado.html">Abrir painel completo →</a>
</body></html>
"""

if __name__ == "__main__":
    main()
