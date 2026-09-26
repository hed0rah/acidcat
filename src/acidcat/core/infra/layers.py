"""Decoded layers: the decoder registry, and the bytes of a Document's layer.

A walker that finds a compressed body declares a layer on the chunk holding it
(node-v1.md section 3) and walks the decoded image as that layer's chunks:

    chunk["layer"] = {"name": "unpacked YM6", "decoder": "lha.lh5",
                      "params": {"size": 140155, "crc": 0x3A51},
                      "length": 140155, "length_known": True,
                      "verdict": {"result": "verified", "method": "crc16",
                                  "detail": "0x3A51"}}
    chunk["layer_chunks"] = [...]     # positioned in the decoded image

The chunk's payload is the layer's one source. Nothing else in the legacy
model reads `layer_chunks`: every consumer of the flat chunk list takes a
chunk's offset as a file offset, which these are not.

The bytes are never stored. `decode()` runs a registered decoder over the
source bytes, bounded by a cap, and `layer_bytes()` re-derives any layer of a
Document from the file's bytes, nested layers included.
"""

from acidcat.core.codecs import lha


class LayerError(ValueError):
    """A layer could not be decoded, or does not match what was declared."""


def _lha_lh5(src, params, cap):
    return lha.unpack_lh5(bytes(src), params["size"], cap)


def _lha_lh0(src, params, cap):
    if len(src) > cap:
        raise LayerError("the stored body is %d bytes; the cap is %d" % (len(src), cap))
    return bytes(src)


def _lha_check(out, params):
    return lha.crc16(out) == params["crc"]


# name -> (decode(src, params, cap), check(out, params) or None, mapping)
# `exact`: layer byte k is source byte k, so a selection maps back to the file
DECODERS = {
    "lha.lh5": (_lha_lh5, _lha_check, "opaque"),
    "lha.lh0": (_lha_lh0, _lha_check, "exact"),
}


def mapping(name):
    return DECODERS[name][2]


def decode(name, src, params, cap):
    """The decoded bytes of `src`, checked when the decoder has a check.
    Raises LayerError on an unknown decoder, a failure, or a failed check."""
    if name not in DECODERS:
        raise LayerError("no decoder %r" % name)
    fn, check, _m = DECODERS[name]
    try:
        out = fn(src, params, cap)
    except LayerError:
        raise
    except (lha.LhaError, KeyError, ValueError) as e:
        raise LayerError("%s: %s" % (name, e)) from None
    if check is not None and not check(out, params):
        raise LayerError("%s: the decoded bytes fail their check" % name)
    return out


def layer_bytes(doc, layer_id, data, cap=None):
    """The bytes of layer `layer_id` of `doc`, given layer 0's bytes `data`.

    Decoded on demand from the layer's source in its parent, under the same
    inflate cap the walk used (`doc["limits"]["inflate_bytes"]` unless `cap`
    is given)."""
    layers = {l["id"]: l for l in doc["layers"]}
    if layer_id not in layers:
        raise LayerError("the document has no layer %d" % layer_id)
    lay = layers[layer_id]
    if lay["kind"] == "file":
        return bytes(data)
    cap = doc["limits"]["inflate_bytes"] if cap is None else cap
    parent = layer_bytes(doc, lay["parent"], data, cap)
    (s,) = lay["sources"]
    src = parent[s["parent_off"]:s["parent_off"] + s["len"]]
    out = decode(lay["decoder"]["name"], src, lay["decoder"].get("params", {}), cap)
    if len(out) != lay["length"]:
        raise LayerError("layer %d decoded to %d bytes; the document says %d"
                         % (layer_id, len(out), lay["length"]))
    return out
