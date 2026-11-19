#!/usr/bin/env python3
"""
Transforma as respostas da API de revendedora em itens de catálogo e gera o HTML.

Pode ser usado sozinho, com JSONs salvos à mão:
    python build_catalogo.py pecas.json materiais.json saida.html

Só vão para o catálogo campos que podem ser públicos.
Nunca saem daqui: nome/ID de cliente, venda, carrinho, nº de série, % de desconto.
"""
import base64
import html as htmllib
import io
import json
import sys
from collections import OrderedDict, defaultdict
from pathlib import Path

LIMITE_FOTO_DIVULGACAO = 2_000_000  # bytes; usado quando as fotos NÃO são baixadas e comprimidas

NORMALIZA_BANHO = {"Rodio": "Ródio", "Ouro/Ródio": "Ouro e Ródio"}
NORMALIZA_SUB = {"Delicadas": "Delicada", "Delicados": "Delicado"}

PASTA = Path(__file__).resolve().parent
TEMPLATE = PASTA / "template.html"


def extrair_pecas(resposta):
    """Aceita {"data": {"pecas": [...]}} (formato atual) e variações simples."""
    if isinstance(resposta, dict):
        data = resposta.get("data", resposta)
        if isinstance(data, dict) and isinstance(data.get("pecas"), list):
            return data["pecas"]
        if isinstance(data, list):
            return data
    if isinstance(resposta, list):
        return resposta
    raise ValueError("Formato inesperado na resposta de peças (não achei a lista 'pecas').")


def extrair_materiais(resposta):
    if not resposta:
        return []
    if isinstance(resposta, dict):
        resposta = resposta.get("data", [])
    return resposta if isinstance(resposta, list) else []


def montar(pecas_raw, materiais_raw=None, limite_bytes=LIMITE_FOTO_DIVULGACAO):
    pecas = extrair_pecas(pecas_raw)
    materiais = extrair_materiais(materiais_raw)

    # Fotos de divulgação (com modelo) por código de modelo, das mais leves para as mais pesadas
    fotos_modelo = defaultdict(list)
    ignoradas = 0
    for m in sorted(materiais, key=lambda x: x.get("tamanho") or 0):
        if m.get("tipo") != "imagem" or not m.get("url"):
            continue
        if limite_bytes and (m.get("tamanho") or 0) > limite_bytes:
            ignoradas += 1
            continue
        for cod in m.get("codigos_modelos") or []:
            if m["url"] not in fotos_modelo[cod.upper()]:
                fotos_modelo[cod.upper()].append(m["url"])

    # Agrupa peças disponíveis por SKU completo (mesmo modelo + banho + pedra)
    grupos = OrderedDict()
    vendidas = 0
    for p in pecas:
        if p.get("venda_id") or p.get("carrinho_item_id") or p.get("data_venda"):
            vendidas += 1
            continue
        sku = p.get("full_sku") or f'{p.get("modelo")}-{p.get("banho")}-{p.get("descricao")}'
        if sku not in grupos:
            imgs = p.get("images") or ([p["imagem"]] if p.get("imagem") else [])
            imgs = sorted(set(imgs), key=lambda u: u.rsplit("-", 1)[-1])
            modelo = str(p.get("modelo") or "").upper()
            descricao = " / ".join(s.strip() for s in str(p.get("descricao") or "").split("/") if s.strip())
            grupos[sku] = {
                "sku": sku,
                "modelo": modelo,
                "descricao": descricao,
                "valor": p.get("valor"),
                "categoria": p.get("categoria") or "Outros",
                "subcategoria": NORMALIZA_SUB.get(p.get("subcategoria"), p.get("subcategoria")),
                "banho": NORMALIZA_BANHO.get(p.get("banho"), p.get("banho")),
                "aro": p.get("aro"),
                "colecao": p.get("colecao"),
                "infantil": p.get("linha") == "infantil",
                "capa": imgs[0] if imgs else None,
                "fotos": imgs,
                "fotos_modelo": list(fotos_modelo.get(modelo, [])),
                "qtd": 0,
            }
        grupos[sku]["qtd"] += 1

    itens = [i for i in grupos.values() if isinstance(i["valor"], (int, float))]
    resumo = {
        "pecas_na_api": len(pecas),
        "vendidas_excluidas": vendidas,
        "pecas_no_catalogo": sum(i["qtd"] for i in itens),
        "itens_no_catalogo": len(itens),
        "fotos_divulgacao_puladas_por_peso": ignoradas,
    }
    return itens, resumo


def _arquivo(pasta, prefixo):
    pasta = Path(pasta)
    if not pasta.is_dir():
        return None
    for arq in sorted(pasta.iterdir()):
        if arq.is_file() and arq.name.lower().startswith(prefixo) and \
                arq.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            return arq
    return None


def _data_uri(dados, mime):
    return f"data:{mime};base64," + base64.b64encode(dados).decode("ascii")


def preparar_imagens(pasta_assets, saida):
    """Foto da revendedora (assets/foto*) e logo (assets/logo*) embutidas no HTML.
    A foto também vira docs/capa.jpg, usada na prévia do link no WhatsApp e como ícone."""
    extra = {}
    foto, logo = _arquivo(pasta_assets, "foto"), _arquivo(pasta_assets, "logo")
    try:
        from PIL import Image, ImageOps
    except ImportError:
        Image = None
    if foto:
        if Image:
            img = ImageOps.exif_transpose(Image.open(foto)).convert("RGB")
            lado = min(img.size)
            img = img.crop(((img.width - lado) // 2, (img.height - lado) // 2,
                            (img.width + lado) // 2, (img.height + lado) // 2))
            pequena = img.resize((240, 240), Image.LANCZOS)
            b = io.BytesIO()
            pequena.save(b, "JPEG", quality=85, optimize=True)
            extra["foto"] = _data_uri(b.getvalue(), "image/jpeg")
            capa = io.BytesIO()
            img.resize((600, 600), Image.LANCZOS).save(capa, "JPEG", quality=85, optimize=True)
            destino = Path(saida) / "capa.jpg"
            if not destino.exists() or destino.read_bytes() != capa.getvalue():
                destino.write_bytes(capa.getvalue())
            extra["capa"] = "capa.jpg"
        else:
            mime = "image/png" if foto.suffix.lower() == ".png" else "image/jpeg"
            extra["foto"] = _data_uri(foto.read_bytes(), mime)
    if logo:
        if Image:
            img = Image.open(logo).convert("RGBA")
            caixa = img.getchannel("A").getbbox()
            if caixa:
                img = img.crop(caixa)
            if img.height > 80:
                img = img.resize((round(img.width * 80 / img.height), 80), Image.LANCZOS)
            b = io.BytesIO()
            img.save(b, "PNG", optimize=True)
            extra["logo"] = _data_uri(b.getvalue(), "image/png")
        else:
            extra["logo"] = _data_uri(logo.read_bytes(), "image/png")
    return extra


def _tags_meta(config, meta):
    e = lambda t: htmllib.escape(str(t), quote=True)
    nome = config.get("nomeLoja", "Catálogo de Joias")
    linhas = [
        f'<meta name="versao-conteudo" content="{e(meta.get("versao", ""))}">',
        '<meta name="theme-color" content="#7b9186">',
        f'<meta name="description" content="{e(meta.get("descricao", ""))}">',
        '<meta property="og:type" content="website">',
        f'<meta property="og:title" content="{e(nome)}">',
        f'<meta property="og:description" content="{e(meta.get("descricao", ""))}">',
    ]
    site = meta.get("site") or ""
    if site:
        linhas.append(f'<meta property="og:url" content="{e(site)}">')
    if config.get("capa"):
        linhas.append(f'<link rel="icon" href="capa.jpg">')
        linhas.append(f'<link rel="apple-touch-icon" href="capa.jpg">')
        if site:
            linhas.append(f'<meta property="og:image" content="{e(site)}capa.jpg?v={e(meta.get("versao", ""))[:8]}">')
            linhas.append('<meta property="og:image:width" content="600">')
            linhas.append('<meta property="og:image:height" content="600">')
    return "\n".join(linhas)


def gerar_html(itens, config, template_path=TEMPLATE, meta=None):
    tpl = Path(template_path).read_text(encoding="utf-8")
    seguro = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    for marcador in ("/*__DADOS__*/[]", "/*__CONFIG__*/{}", "<!--__META__-->", "<title>Catálogo de Joias</title>"):
        if marcador not in tpl:
            raise ValueError(f"template.html sem o marcador {marcador}")
    nome = htmllib.escape(config.get("nomeLoja", "Catálogo de Joias"))
    return (tpl.replace("/*__DADOS__*/[]", seguro(itens))
               .replace("/*__CONFIG__*/{}", seguro(config))
               .replace("<!--__META__-->", _tags_meta(config, meta or {}))
               .replace("<title>Catálogo de Joias</title>", f"<title>{nome}</title>"))


def main():
    if len(sys.argv) != 4:
        print(__doc__)
        sys.exit(1)
    pecas_path, mat_path, out_path = sys.argv[1:]
    carregar = lambda c: json.loads(Path(c).read_text(encoding="utf-8"))
    itens, resumo = montar(carregar(pecas_path), carregar(mat_path))
    Path(out_path).write_text(gerar_html(itens, {}), encoding="utf-8")
    print(json.dumps(resumo, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
