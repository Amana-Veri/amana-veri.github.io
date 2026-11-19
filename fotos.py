"""
Baixa as fotos das peças, comprime e guarda junto com o catálogo.

Para cada foto gera:
  <hash>.jpg    versão grande (até 1200 px) para a tela de detalhe
  <hash>_c.jpg  versão capa (até 500 px) para a grade
Fotos já baixadas em execuções anteriores são reaproveitadas; as que não são mais usadas são apagadas.
Se uma foto não puder ser baixada, a peça continua com o link original da marca.
"""
import hashlib
import io
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

MAX_DOWNLOAD = 40_000_000  # bytes
TAM_GRANDE, TAM_CAPA = 1200, 500
UA = "Mozilla/5.0 (catalogo-revendedora)"


def _nome(url):
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _salvar(img, caminho, lado, qualidade):
    from PIL import Image
    copia = img.copy()
    copia.thumbnail((lado, lado), Image.LANCZOS)
    tmp = caminho.with_name(caminho.name + ".tmp")
    copia.save(tmp, "JPEG", quality=qualidade, optimize=True, progressive=True)
    tmp.replace(caminho)


def _baixar(url, pasta):
    from PIL import Image, ImageOps
    base = _nome(url)
    grande, capa = pasta / f"{base}.jpg", pasta / f"{base}_c.jpg"
    if grande.exists() and capa.exists():
        return url, True, False
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Referer": "https://vq-app.vercel.app/"})
        with urllib.request.urlopen(req, timeout=90) as r:
            dados = r.read(MAX_DOWNLOAD + 1)
        if len(dados) > MAX_DOWNLOAD:
            return url, False, False
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(dados)))
        if img.mode in ("RGBA", "LA", "P"):
            fundo = Image.new("RGB", img.size, (255, 255, 255))
            img = img.convert("RGBA")
            fundo.paste(img, mask=img.split()[-1])
            img = fundo
        img = img.convert("RGB")
        _salvar(img, grande, TAM_GRANDE, 82)
        _salvar(img, capa, TAM_CAPA, 78)
        return url, True, True
    except Exception:
        return url, False, False


def processar_fotos(itens, pasta, prefixo="fotos/"):
    try:
        import PIL  # noqa: F401
    except ImportError:
        print("Aviso: Pillow não instalado; as fotos continuam como links da marca.")
        return {"fotos": "links da marca (Pillow ausente)"}

    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    urls = sorted({u for i in itens for u in i["fotos"] + i["fotos_modelo"]})
    with ThreadPoolExecutor(max_workers=6) as ex:
        resultados = list(ex.map(lambda u: _baixar(u, pasta), urls))
    ok = {u for u, sucesso, _ in resultados if sucesso}
    novas = sum(1 for _, s, nova in resultados if s and nova)

    local = lambda u, suf="": f"{prefixo}{_nome(u)}{suf}.jpg"
    for i in itens:
        if i["capa"] in ok:
            i["capa"] = local(i["capa"], "_c")
        i["fotos"] = [local(u) if u in ok else u for u in i["fotos"]]
        # fotos de divulgação originais podem ter 10+ MB: só entram se foram comprimidas
        i["fotos_modelo"] = [local(u) for u in i["fotos_modelo"] if u in ok]

    usados = {f"{_nome(u)}{s}.jpg" for u in ok for s in ("", "_c")}
    apagadas = 0
    for arq in pasta.glob("*.jpg"):
        if arq.name not in usados:
            arq.unlink()
            apagadas += 1
    return {
        "fotos_ok": len(ok),
        "fotos_novas_baixadas": novas,
        "fotos_com_falha": len(urls) - len(ok),
        "fotos_antigas_apagadas": apagadas,
    }
