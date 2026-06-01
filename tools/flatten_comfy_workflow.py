#!/usr/bin/env python3
"""
flatten_comfy_workflow.py — convert a ComfyUI *subgraph* workflow (.json, UI
format) into a flat **API-prompt** graph that ComfyUI's /prompt endpoint accepts.

Why: ComfyUI's bundled workflows (e.g. the AEON-Spark FLUX.2 / LTX-2.3 graphs)
wrap everything in a "subgraph" node. The /prompt API needs the flattened,
executable form. The ComfyUI *frontend* does this flattening in JavaScript at
queue time; there is no server endpoint for it. This tool reproduces that
flattening headlessly so we can freeze a known-good graph as a template that the
`spark comfy generate/animate` commands load and parameterise at runtime.

Pipeline:
    workflow.json  --(this tool, run once)-->  templates/<name>.json  --(spark)-->  ComfyUI

Usage:
    # against a RUNNING ComfyUI (it reads node schemas from /object_info):
    python3 tools/flatten_comfy_workflow.py <workflow.json> [--comfy URL] > templates/foo.json

The emitted template keeps the parameterisable nodes intact so the CLI can patch
them by class_type at runtime:
  - LoadImage         -> set inputs.image  (one per image subgraph-input, in order)
  - PrimitiveStringMultiline / positive CLIPTextEncode -> set the prompt text
  - RandomNoise       -> set inputs.noise_seed
  - SaveImage/SaveVideo -> filename_prefix / format

Gotchas handled (each was a real bug while building `spark comfy animate`):
  - Reroute nodes are UI-only passthroughs -> links resolved through them.
  - object_info COMBO inputs are ["COMBO", {...}] in current ComfyUI (the type is
    the *string* "COMBO", not a list of options).
  - widgets_values includes a slot for widgets that were promoted to inputs/
    connections — those slots must be CONSUMED (index advanced) even though the
    value isn't used, or later widgets land on the wrong value (e.g. batch_size
    got a spatial dim -> tensor-size crash).
  - ResizeImageMaskNode has a templated `resize_type` widget (mode + .width/
    .height/.crop) that breaks positional mapping — handled explicitly.
  - subgraph inputs appear as links from a virtual origin id (-10); slot N maps
    to the Nth declared subgraph input.
"""
import argparse, json, sys, urllib.request

CONTROL = {"fixed", "randomize", "increment", "decrement"}


def widget_names(oi, t):
    """Ordered (name) of widget-type inputs for a node type, from /object_info."""
    spec = oi.get(t, {}).get("input", {})
    names = []
    for sect in ("required", "optional"):
        for name, val in spec.get(sect, {}).items():
            typ = val[0] if isinstance(val, list) else val
            if typ == "COMBO" or isinstance(typ, list) or typ in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                names.append(name)
    return names


def flatten(workflow, oi):
    w = workflow
    if not w.get("definitions", {}).get("subgraphs"):
        raise SystemExit("no subgraph in this workflow — already flat, or unsupported shape")
    sg = w["definitions"]["subgraphs"][0]
    nodes = {n["id"]: n for n in sg["nodes"]}
    incoming = {(l["target_id"], l["target_slot"]): (l["origin_id"], l["origin_slot"]) for l in sg["links"]}

    def resolve(oid, oslot):
        seen = 0
        while oid in nodes and nodes[oid]["type"] == "Reroute" and seen < 32:
            s = incoming.get((oid, 0))
            if s is None:
                return None
            oid, oslot = s
            seen += 1
        return (oid, oslot)

    # image subgraph-inputs (type mentions IMAGE) -> a LoadImage each, in slot order
    image_slots = [i for i, inp in enumerate(sg.get("inputs", [])) if "IMAGE" in str(inp.get("type", ""))]
    img_loader = {}
    api = {}
    for n, slot in enumerate(image_slots):
        nid = f"900{n+1}"
        api[nid] = {"class_type": "LoadImage", "inputs": {"image": "PLACEHOLDER.png"}}
        img_loader[slot] = nid

    # positive prompt node = target of the first STRING subgraph-input
    string_slots = [i for i, inp in enumerate(sg.get("inputs", [])) if inp.get("type") == "STRING"]
    prompt_node = None
    if string_slots:
        for l in sg["links"]:
            if l["origin_id"] == -10 and l["origin_slot"] == string_slots[0]:
                prompt_node = l["target_id"]

    out_src = None
    for l in sg["links"]:
        if l["target_id"] < 0:
            out_src = resolve(l["origin_id"], l["origin_slot"])

    for nid, node in nodes.items():
        t = node["type"]
        if t == "Reroute":
            continue
        inp = {}
        for slot, ie in enumerate(node.get("inputs", [])):
            nm = ie.get("name")
            src = incoming.get((nid, slot))
            if src is None:
                continue
            r = resolve(*src)
            if r is None:
                continue
            oid, oslot = r
            if oid == -10:                      # a subgraph input
                if oslot in img_loader:          # an image input -> its LoadImage
                    inp[nm] = [img_loader[oslot], 0]
                continue                         # other subgraph inputs fall back to widget
            inp[nm] = [str(oid), oslot]
        wv = list(node.get("widgets_values") or [])
        wi = 0
        for nm in widget_names(oi, t):
            if wi >= len(wv):
                break
            if nm in inp:                        # satisfied by a connection: consume slot, don't assign
                wi += 1
                continue
            inp[nm] = wv[wi]
            wi += 1
            if wi < len(wv) and isinstance(wv[wi], str) and wv[wi] in CONTROL:
                wi += 1
        if t == "ResizeImageMaskNode" and len(wv) >= 5:
            inp["resize_type"] = wv[0]
            inp["resize_type.crop"] = wv[3]
            inp["scale_method"] = wv[-1]
        api[str(nid)] = {"class_type": t, "inputs": inp}

    if out_src:
        out_type = sg.get("outputs", [{}])[0].get("type", "")
        if out_type == "VIDEO":
            api["9002"] = {"class_type": "SaveVideo",
                           "inputs": {"video": [str(out_src[0]), out_src[1]],
                                      "filename_prefix": "spark", "format": "auto", "codec": "auto"}}
        else:
            api["9002"] = {"class_type": "SaveImage",
                           "inputs": {"images": [str(out_src[0]), out_src[1]], "filename_prefix": "spark"}}

    meta = {"_image_loaders": list(img_loader.values()), "_prompt_node": str(prompt_node) if prompt_node else None}
    return api, meta


def main():
    ap = argparse.ArgumentParser(description="Flatten a ComfyUI subgraph workflow to an API template.")
    ap.add_argument("workflow", help="path to the ComfyUI workflow .json (UI format, with a subgraph)")
    ap.add_argument("--comfy", default="http://localhost:8188", help="running ComfyUI base URL (for /object_info)")
    args = ap.parse_args()
    oi = json.load(urllib.request.urlopen(args.comfy + "/object_info", timeout=60))
    api, meta = flatten(json.load(open(args.workflow)), oi)
    sys.stderr.write(f"# flattened: {len(api)} nodes  image_loaders={meta['_image_loaders']}  prompt_node={meta['_prompt_node']}\n")
    print(json.dumps(api, indent=1))


if __name__ == "__main__":
    main()
