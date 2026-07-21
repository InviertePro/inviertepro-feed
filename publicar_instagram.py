#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
InviertePro · Publicador automático de Instagram (Meta Graph API)
=================================================================
Publica las piezas del feed usando la API oficial de Instagram.
Solo usa la librería estándar de Python (no requiere instalar nada).

CÓMO SE USA
-----------
1) Configura tus credenciales (una sola vez). Dos opciones:
   a) Variables de entorno:
        export IG_USER_ID="1789xxxxxxxxxxxx"
        export IG_TOKEN="EAAG...tu_token_largo..."
   b) O crea un archivo  config.json  al lado de este script:
        { "ig_user_id": "1789...", "token": "EAAG..." }
   (config.json es privado: NO lo subas a ningún repo público.)

2) Asegúrate de que manifest.json tenga el BASE_URL correcto
   (dónde están hospedadas las imágenes públicamente).

3) Comandos:
     python3 publicar_instagram.py --status          # ver qué falta por publicar
     python3 publicar_instagram.py --dry-run --next   # simular la próxima (no publica)
     python3 publicar_instagram.py --next             # publica LA próxima lista
     python3 publicar_instagram.py --id C-001         # publica una específica
     python3 publicar_instagram.py --all-ready        # publica todas las listas (respeta límite)
     python3 publicar_instagram.py --reels-as-carousel --id R-001   # publica un reel como carrusel de imágenes

NOTAS
-----
- La API publica al instante (no agenda). Para "piloto automático" se corre
  este script 1 vez por día (ej. con una tarea programada) usando --next.
- Los reels necesitan un video_url real en el manifest. Sin video se saltan,
  salvo que uses --reels-as-carousel para publicar sus frames como carrusel.
- Límite de Instagram: 50 publicaciones por 24h. El script respeta un tope.
"""
import os, sys, json, time, argparse, urllib.parse, urllib.request, urllib.error

GRAPH = "https://graph.facebook.com/v21.0"
HERE = os.path.dirname(os.path.abspath(__file__))
MANIFEST = os.path.join(HERE, "manifest.json")
DAILY_LIMIT = 25  # tope de seguridad por corrida

# ---------- credenciales ----------
def load_creds():
    uid = os.environ.get("IG_USER_ID")
    tok = os.environ.get("IG_TOKEN")
    cfg = os.path.join(HERE, "config.json")
    if (not uid or not tok) and os.path.exists(cfg):
        c = json.load(open(cfg, encoding="utf-8"))
        uid = uid or c.get("ig_user_id")
        tok = tok or c.get("token")
    if not uid or not tok:
        sys.exit("✗ Faltan credenciales. Define IG_USER_ID e IG_TOKEN "
                 "(variables de entorno) o crea config.json. Ver instrucciones arriba.")
    return uid, tok

# ---------- helpers HTTP ----------
def _call(method, path, params):
    url = f"{GRAPH}/{path}"
    data = urllib.parse.urlencode(params).encode()
    if method == "GET":
        url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, method="GET")
    else:
        req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"API error {e.code}: {body}")

def create_media(uid, tok, params):
    params["access_token"] = tok
    return _call("POST", f"{uid}/media", params)["id"]

def publish_media(uid, tok, creation_id):
    return _call("POST", f"{uid}/media_publish",
                 {"creation_id": creation_id, "access_token": tok})["id"]

def container_status(cid, tok):
    return _call("GET", cid, {"fields": "status_code", "access_token": tok}).get("status_code")

def wait_ready(cid, tok, tries=30, delay=4):
    """Espera a que un contenedor quede FINISHED antes de publicar.
    Evita el error 9007 'El archivo multimedia no está listo para publicar'."""
    st = None
    for _ in range(tries):
        st = container_status(cid, tok)
        if st == "FINISHED":
            return True
        if st in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"El contenedor quedó en estado {st}.")
        time.sleep(delay)
    raise RuntimeError(f"Timeout esperando que el contenido quede listo (último estado: {st}).")

# ---------- publicación por tipo ----------
def pub_single(uid, tok, url, caption):
    cid = create_media(uid, tok, {"image_url": url, "caption": caption})
    wait_ready(cid, tok)
    return publish_media(uid, tok, cid)

def pub_carousel(uid, tok, urls, caption):
    children = []
    for u in urls[:10]:
        child = create_media(uid, tok, {"image_url": u, "is_carousel_item": "true"})
        wait_ready(child, tok)          # esperar que cada imagen quede lista
        children.append(child)
    cid = create_media(uid, tok, {
        "media_type": "CAROUSEL",
        "children": ",".join(children),
        "caption": caption
    })
    wait_ready(cid, tok)                 # esperar que el carrusel completo quede listo
    return publish_media(uid, tok, cid)

def pub_reel(uid, tok, video_url, caption):
    cid = create_media(uid, tok, {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": caption,
        "share_to_feed": "true"
    })
    # el video se procesa: esperar a FINISHED
    for _ in range(40):
        st = container_status(cid, tok)
        if st == "FINISHED":
            break
        if st == "ERROR":
            raise RuntimeError("El procesamiento del video falló (status ERROR).")
        time.sleep(6)
    else:
        raise RuntimeError("Timeout esperando el procesamiento del video.")
    return publish_media(uid, tok, cid)

# ---------- lógica ----------
def load_manifest():
    return json.load(open(MANIFEST, encoding="utf-8"))

def save_manifest(m):
    json.dump(m, open(MANIFEST, "w", encoding="utf-8"), ensure_ascii=False, indent=2)

def is_ready(p, reels_as_carousel):
    if p.get("publicado"):
        return False
    if p["formato"] == "reel" and not reels_as_carousel:
        return bool(p.get("video_url"))
    return True

def publish_post(uid, tok, p, reels_as_carousel, dry):
    fmt = p["formato"]
    urls = p.get("image_urls", [])
    cap = p["caption"]
    if fmt == "reel" and not reels_as_carousel:
        if dry: return f"[DRY] reel {p['id']} → video {p.get('video_url')}"
        mid = pub_reel(uid, tok, p["video_url"], cap)
    elif fmt == "single":
        if dry: return f"[DRY] single {p['id']} → {urls[0]}"
        mid = pub_single(uid, tok, urls[0], cap)
    else:  # carousel (o reel forzado a carrusel)
        if dry: return f"[DRY] carrusel {p['id']} → {len(urls)} imágenes"
        mid = pub_carousel(uid, tok, urls, cap)
    return mid

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", action="store_true", help="Mostrar estado y salir")
    ap.add_argument("--next", action="store_true", help="Publicar la próxima lista")
    ap.add_argument("--id", help="Publicar una pieza específica por ID (ej. C-001)")
    ap.add_argument("--all-ready", action="store_true", help="Publicar todas las listas")
    ap.add_argument("--reels-as-carousel", action="store_true",
                    help="Publicar reels como carrusel de imágenes (sin video)")
    ap.add_argument("--dry-run", action="store_true", help="Simular, sin publicar")
    args = ap.parse_args()

    m = load_manifest()
    posts = m["posts"]

    if m.get("base_url", "").startswith("https://TU-HOST"):
        print("⚠  OJO: manifest.json todavía tiene el BASE_URL de ejemplo. "
              "Cámbialo por tu host real antes de publicar.\n")

    if args.status:
        pub = sum(1 for p in posts if p.get("publicado"))
        print(f"Cuenta: {m.get('cuenta')}  ·  {pub}/{len(posts)} publicadas\n")
        for p in posts:
            estado = "✓ publicada" if p.get("publicado") else "· pendiente"
            extra = "  ⚑ falta video" if (p["formato"] == "reel" and not p.get("video_url") and not p.get("publicado")) else ""
            print(f"  {p['id']:6} {p['formato']:8} {estado}{extra}")
        return

    uid, tok = load_creds()

    if args.id:
        sel = [p for p in posts if p["id"] == args.id]
        if not sel:
            sys.exit(f"✗ No encontré la pieza {args.id}")
    elif args.next:
        sel = next(([p] for p in posts if is_ready(p, args.reels_as_carousel)), [])
        if not sel:
            print("Nada pendiente y listo para publicar. 🎉"); return
    elif args.all_ready:
        sel = [p for p in posts if is_ready(p, args.reels_as_carousel)][:DAILY_LIMIT]
        if not sel:
            print("Nada pendiente y listo para publicar. 🎉"); return
    else:
        ap.print_help(); return

    errores = []
    for p in sel:
        ok = False
        for intento in (1, 2):
            try:
                print(f"→ Publicando {p['id']} ({p['formato']}) … (intento {intento})")
                res = publish_post(uid, tok, p, args.reels_as_carousel, args.dry_run)
                if args.dry_run:
                    print(f"   {res}")
                else:
                    p["publicado"] = True
                    p["media_id"] = res
                    p["publicado_en"] = time.strftime("%Y-%m-%d %H:%M:%S")
                    save_manifest(m)
                    print(f"   ✓ Publicada. media_id={res}")
                ok = True
                break
            except Exception as e:
                print(f"   ✗ Error en {p['id']} (intento {intento}): {e}")
                if intento == 1:
                    time.sleep(15)  # reintenta una vez tras una pausa
        if not ok:
            errores.append(p["id"])
        time.sleep(8)  # respiro entre publicaciones (no satura la API)
    if errores:
        print(f"\n⚠ Terminó con {len(errores)} pendiente(s) por error: {errores}. "
              "Vuelve a correr el workflow para reintentarlas (las publicadas no se repiten).")
    else:
        print("\n✓ Listo, todo publicado sin errores.")

if __name__ == "__main__":
    main()
