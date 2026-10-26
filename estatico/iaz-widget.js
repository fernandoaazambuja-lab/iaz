// iAz · AzamInsights — widget do iPhone (app Scriptable)
// Lê os números publicados a cada 5 min em fernandoaazambuja-lab.github.io/iaz e mostra na tela inicial.
// Tamanhos: pequeno (4 linhas), médio (6 marcadas como "medio"), grande (todas, agrupadas) e tela de bloqueio (3).
const URL_DADOS = "https://fernandoaazambuja-lab.github.io/iaz/dados.json"
const URL_SITE = "https://fernandoaazambuja-lab.github.io/iaz/"
const fam = config.widgetFamily || "medium"
const LINHAS = { small: 4, medium: 6, large: 15 }

let d = null
try {
  d = await new Request(URL_DADOS + "?t=" + Date.now()).loadJSON()
  Keychain.set("iaz_cache", JSON.stringify(d))
} catch (e) {
  // sem internet: mostra o último dado guardado, marcado com ⚠︎
  if (Keychain.contains("iaz_cache")) { d = JSON.parse(Keychain.get("iaz_cache")); d.antigo = true }
}

const VERDE = new Color("#123A2E"), VERDE2 = new Color("#0B231C"), OURO = new Color("#EDB84A")
const BRANCO = Color.white(), CINZA = new Color("#9DB5AC")
const SOBE = new Color("#4ADE8F"), DESCE = new Color("#FF7A6E")

const w = new ListWidget()
w.url = URL_SITE
w.refreshAfterDate = new Date(Date.now() + 5 * 60 * 1000)

if (fam.startsWith("accessory")) {
  // tela de bloqueio: o iOS pinta em monocromático, então só texto
  w.addAccessoryWidgetBackground = true
  const itens = d ? d.itens.filter(i => ["Dólar", "Soja B3", "Troca ureia"].includes(i.nome)) : []
  for (const it of itens) {
    const t = w.addText(`${it.nome.replace("Troca ", "")} ${it.valor} ${it.var}`)
    t.font = Font.mediumMonospacedSystemFont(11)
    t.lineLimit = 1
    t.minimumScaleFactor = 0.7
  }
  if (!itens.length) w.addText("iAz sem dados")
} else {
  const g = new LinearGradient()
  g.colors = [VERDE, VERDE2]
  g.locations = [0, 1]
  w.backgroundGradient = g
  w.setPadding(12, 14, 12, 14)

  const topo = w.addStack()
  topo.centerAlignContent()
  const i = topo.addText("i"); i.font = Font.heavySystemFont(15); i.textColor = OURO
  const az = topo.addText("Az"); az.font = Font.heavySystemFont(15); az.textColor = BRANCO
  topo.addSpacer()
  const hora = topo.addText(d ? (d.antigo ? "⚠︎ " : "") + d.hora : "--:--")
  hora.font = Font.mediumMonospacedSystemFont(11); hora.textColor = CINZA
  w.addSpacer(fam === "large" ? 10 : 6)

  if (!d) {
    const t = w.addText("Sem conexão com o iAz"); t.textColor = CINZA; t.font = Font.systemFont(12)
  } else {
    const pequeno = fam === "small"
    const lista = fam === "large" ? d.itens : d.itens.filter(i => i.medio !== false)
    let grupoAnt = null
    for (const it of lista.slice(0, LINHAS[fam] || 6)) {
      if (fam === "large" && grupoAnt && it.grupo !== grupoAnt) w.addSpacer(4)   // respiro entre assuntos
      grupoAnt = it.grupo
      const l = w.addStack()
      l.centerAlignContent()
      const nome = l.addText(pequeno ? it.nome : `${it.nome}${it.ctr ? " " + it.ctr : ""}`)
      nome.font = Font.systemFont(pequeno ? 11 : 12); nome.textColor = BRANCO
      nome.lineLimit = 1; nome.minimumScaleFactor = 0.75
      l.addSpacer()
      const v = l.addText(it.valor)
      v.font = Font.semiboldMonospacedSystemFont(pequeno ? 11 : 12); v.textColor = BRANCO
      v.lineLimit = 1; v.minimumScaleFactor = 0.75
      if (!pequeno) {
        const c = l.addStack(); c.size = new Size(66, 0); c.addSpacer()
        const t = c.addText(it.var || "")
        t.font = Font.mediumMonospacedSystemFont(11)
        t.textColor = it.dir === "up" ? SOBE : it.dir === "down" ? DESCE : CINZA
      }
      w.addSpacer(fam === "large" ? 2 : 3)
    }
  }
}

Script.setWidget(w)
if (config.runsInApp) await w.presentMedium()   // ao rodar dentro do app, mostra a prévia
Script.complete()
