"""
Usa as fotos do site oficial da Veri (porveri.com.br) quando a peça existe lá.

Como funciona
  - O site (Nuvemshop) usa o MESMO SKU completo do app de revendedora
    (ex.: br006426-01-1200-0800-0000-02). Só trocamos a foto quando o SKU bate:
    exato, ou igual nos 5 primeiros blocos (modelo, banho e pedras), que é o que define
    a aparência da peça. Banho ou pedra diferente nunca é aceito.
  - O índice SKU -> página vem das páginas de listagem /produtos/page/N/ (permitidas no
    robots.txt; a busca /search/ é proibida para robôs e NÃO é usada).
  - A galeria de cada produto vem do sitemap do site, que lista as fotos de cada página.
  - O índice fica guardado em cache_site.json e só é refeito quando tem mais de 7 dias
    ou quando chega uma peça nova que ainda não foi procurada (no máximo 1x por dia).
    Para forçar: apague o cache_site.json no GitHub e rode o workflow.
  - Se o site estiver fora do ar ou mudar, o catálogo segue com as fotos do app.
"""
import gzip
import html
import json
import re
import time
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

SITE = "https://porveri.com.br"
UA = "Mozilla/5.0 (compatible; catalogo-revendedora-veri/1.0)"
PAUSA = 1.5            # segundos entre páginas, para não sobrecarregar o site
MAX_PAGINAS = 120
IDADE_MAX = timedelta(days=7)
IDADE_MIN_PARA_REFAZER = timedelta(days=1)


def _baixar(url, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "pt-BR"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        dados = r.read()
    if dados[:2] == b"\x1f\x8b":
        dados = gzip.decompress(dados)
    return dados.decode("utf-8", errors="replace")


def _https(u):
    u = (u or "").replace("\\/", "/")
    if u.startswith("//"):
        return "https:" + u
    return u.replace("http://", "https://", 1)


def _sem_query(u):
    return u.split("?", 1)[0]


class Robos:
    def __init__(self):
        self.rp = urllib.robotparser.RobotFileParser()
        self.rp.parse(_baixar(f"{SITE}/robots.txt").splitlines())

    def pode(self, url):
        return self.rp.can_fetch(UA, url)


def galerias_do_sitemap(robos):
    """{url_do_produto: [fotos em ordem]} a partir do sitemap (com <image:loc>)."""
    url = f"{SITE}/sitemap.xml.gz"
    if not robos.pode(url):
        return {}
    xml = _baixar(url, timeout=120)
    galerias = {}
    for bloco in re.findall(r"<url>(.*?)</url>", xml, re.S):
        loc = re.search(r"<loc>([^<]+)</loc>", bloco)
        if not loc or "/produtos/" not in loc.group(1):
            continue
        fotos = [_https(html.unescape(f)) for f in re.findall(r"<image:loc>([^<]+)</image:loc>", bloco)]
        if fotos:
            galerias[_sem_query(loc.group(1).strip())] = fotos
    return galerias


def _itens_da_listagem(pagina_html):
    """Cada produto da listagem: link, variantes (sku, foto da variante, disponível)."""
    partes = pagina_html.split('class="js-item-product')[1:]
    for parte in partes:
        link = re.search(r'href="(https://porveri\.com\.br/produtos/[^"]+)"', parte)
        dv = re.search(r'data-variants="([^"]*)"', parte)
        if not link or not dv:
            continue
        try:
            variantes = json.loads(html.unescape(dv.group(1)))
        except json.JSONDecodeError:
            continue
        yield _sem_query(link.group(1)), [
            {"sku": str(v.get("sku") or "").strip().lower(),
             "foto": _https(v.get("image_url")) if v.get("image_url") else None}
            for v in variantes if isinstance(v, dict)
        ]


def indice_da_listagem(robos, log=print):
    """{sku: {"url":..., "foto":..., "outras":[fotos das outras variantes do mesmo produto]}}"""
    indice, pagina = {}, 1
    while pagina <= MAX_PAGINAS:
        url = f"{SITE}/produtos/" if pagina == 1 else f"{SITE}/produtos/page/{pagina}/"
        if not robos.pode(url):
            log(f"robots.txt não permite {url}; parei.")
            break
        try:
            conteudo = _baixar(url)
        except Exception as e:  # página inexistente = fim da listagem
            if pagina == 1:
                raise
            log(f"Listagem terminou na página {pagina - 1} ({e.__class__.__name__}).")
            break
        novos = 0
        for link, variantes in _itens_da_listagem(conteudo):
            fotos_var = [v["foto"] for v in variantes if v["foto"]]
            for v in variantes:
                if v["sku"] and v["sku"] not in indice:
                    indice[v["sku"]] = {
                        "url": link,
                        "foto": v["foto"],
                        "outras": [f for f in fotos_var if f != v["foto"]],
                    }
                    novos += 1
        if novos == 0:
            break
        if f'/produtos/page/{pagina + 1}/' not in conteudo:
            break
        pagina += 1
        time.sleep(PAUSA)
    return indice, pagina


def _agora():
    return datetime.now(timezone.utc)


def carregar_cache(caminho):
    try:
        return json.loads(Path(caminho).read_text(encoding="utf-8"))
    except Exception:
        return {}


def precisa_refazer(cache, skus_procurados):
    quando = cache.get("atualizado_em")
    if not quando or not cache.get("skus"):
        return True
    idade = _agora() - datetime.fromisoformat(quando)
    if idade > IDADE_MAX:
        return True
    # só refaz antes de 7 dias se chegou peça NOVA (que ainda não foi procurada) e não está no índice
    ja_procurados = set(cache.get("procurados") or [])
    novas = [s for s in skus_procurados if s not in ja_procurados and achar(cache["skus"], s)[0] is None]
    return bool(novas) and idade > IDADE_MIN_PARA_REFAZER


def atualizar_cache(caminho, skus_procurados, forcar=False, log=print):
    cache = carregar_cache(caminho)
    if not forcar and not precisa_refazer(cache, skus_procurados):
        return cache, False
    robos = Robos()
    galerias = galerias_do_sitemap(robos)
    indice, paginas = indice_da_listagem(robos, log=log)
    if len(indice) < 50:  # algo deu errado; mantém o cache antigo
        raise RuntimeError(f"índice do site veio pequeno demais ({len(indice)} SKUs)")
    urls = {info["url"] for info in indice.values()}
    novo = {
        "atualizado_em": _agora().isoformat(timespec="seconds"),
        "paginas": paginas,
        "skus": indice,
        "galerias": {u: g for u, g in galerias.items() if u in urls},
        "procurados": sorted(set(skus_procurados)),
    }
    tmp = Path(caminho).with_suffix(".tmp")
    tmp.write_text(json.dumps(novo, ensure_ascii=False, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    tmp.replace(caminho)
    log(f"Índice do site refeito: {len(indice)} SKUs em {paginas} páginas.")
    return novo, True


def _chave5(sku):
    return "-".join(sku.split("-")[:5])


def achar(indice, sku):
    """Busca exata; se não achar, pelos 5 primeiros blocos (modelo, banho, pedras)."""
    sku = sku.strip().lower()
    if sku in indice:
        return indice[sku], "exato"
    alvo = _chave5(sku)
    if alvo.count("-") < 4:
        return None, None
    for k in sorted(indice):
        if _chave5(k) == alvo:
            return indice[k], "parcial"
    return None, None


def fotos_oficiais(info, galerias):
    """Foto da variante primeiro; depois a galeria, sem fotos de outras variantes (outro banho/cor)."""
    outras = set(info.get("outras") or [])
    ordem = [info.get("foto")] + [f for f in galerias.get(info["url"], []) if f not in outras]
    vistas, saida = set(), []
    for f in ordem:
        if f and f not in vistas:
            vistas.add(f)
            saida.append(f)
    return saida


def aplicar(itens, caminho_cache, forcar=False, log=print):
    """Troca as fotos das peças encontradas no site. Nunca derruba o catálogo."""
    skus = [i["sku"].strip().lower() for i in itens if i.get("sku")]
    try:
        cache, refeito = atualizar_cache(caminho_cache, skus, forcar=forcar, log=log)
    except Exception as e:
        log(f"Aviso: não consegui ler o site da Veri ({e.__class__.__name__}: {e}). Usando o que houver em cache.")
        cache, refeito = carregar_cache(caminho_cache), False
    indice = cache.get("skus") or {}
    galerias = cache.get("galerias") or {}
    trocadas = 0
    for i in itens:
        info, _tipo = achar(indice, i.get("sku") or "")
        if not info:
            continue
        oficiais = fotos_oficiais(info, galerias)
        if not oficiais:
            continue
        i["fotos"] = oficiais
        i["capa"] = oficiais[0]
        trocadas += 1
    return {"fotos_do_site_veri": f"{trocadas} de {len(itens)} peças", "indice_site_refeito": refeito}
