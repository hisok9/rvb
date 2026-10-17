#!/usr/bin/env python3
"""Drop Android 12+ split-type requirements from a binary AndroidManifest.xml.

A base APK extracted from a Play Store split install can declare
``android:requiredSplitTypes="base__abi,base__density"`` on ``<manifest>``.
Android 12+ enforces that declaration: install the base alone — without split
APKs providing every listed type in the same session — and the package manager
refuses with ``INSTALL_FAILED_MISSING_SPLIT`` (surfaced to PackageInstaller
callers as ``STATUS_FAILURE_INCOMPATIBLE``, i.e. the "app is incompatible"
error). Single-APK outputs never carry those config splits, so an unstripped
declaration makes the artifact uninstallable on every Android 12+ device.

Scope: only the declaring attribute records are removed. The string pool, the
resource map and every other element stay byte-identical. ``meta-data
com.android.vending.splits.required`` is Play-side metadata the platform never
enforces and is intentionally left alone. The ``<application>``
``isSplitRequired`` flag (the pre-12 spelling) is dropped too when true.

In-place edit of a manifest file (the rvb prep wrapper unpacks and repacks the
archive itself), or of a whole APK when given one directly (the archive is
rewritten with entry order, metadata and per-entry compression preserved).
The script verifies its own output by re-parsing before replacing the input.

Exit codes (both modes):
  0  nothing to remove; input untouched
 10  requirement found and removed; input rewritten
  1  requirement present but could not be safely removed (input untouched)
"""
import os
import struct
import sys
import zipfile

RES_STRING_POOL = 0x0001
RES_XML_START_ELEMENT = 0x0102

TYPE_STRING = 0x03
TYPE_BOOLEAN = 0x12

# element name -> attribute names that gate installation
DROP = {
    "manifest": ("requiredSplitTypes", "splitTypes"),
    "application": ("isSplitRequired",),
}


def u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def parse_string_pool(data, off):
    typ, hdr_size, size = struct.unpack_from("<HHI", data, off)
    if typ != RES_STRING_POOL:
        raise ValueError("not a string pool chunk")
    str_count, _style_count, flags, strings_start = struct.unpack_from(
        "<IIII", data, off + 8
    )
    utf8 = bool(flags & (1 << 8))
    idxs = [u32(data, off + hdr_size + 4 * i) for i in range(str_count)]
    out = []
    for si in idxs:
        p = off + strings_start + si
        if utf8:
            def var8(d, o):
                b = d[o]
                if b & 0x80:
                    return ((b & 0x7F) << 8) | d[o + 1], o + 2
                return b, o + 2
            _chars, p = var8(data, p)
            blen, p = var8(data, p)
            out.append(data[p : p + blen].decode("utf-8", "replace"))
        else:
            ln = u16(data, p)
            p += 2
            if ln & 0x8000:
                ln = ((ln & 0x7FFF) << 16) | u16(data, p)
                p += 2
            out.append(data[p : p + 2 * ln].decode("utf-16-le", "replace"))
    return out, size


def attr_value(pool, data_type, data):
    if data_type == TYPE_STRING:
        return pool[data]
    if data_type == TYPE_BOOLEAN:
        return "true" if data else "false"
    return None


def walk(data):
    """Yield (chunk_offset, element_name, attrs) for every START_ELEMENT.

    attrs is a list of (name, value_or_None, offset_in_attr_array).
    """
    if u16(data, 0) != 0x0003:  # RES_XML_TYPE
        raise ValueError("not a binary XML document")
    xml_hdr = u16(data, 2)
    pool, pool_size = parse_string_pool(data, xml_hdr)
    o = xml_hdr + pool_size
    while o + 8 <= len(data):
        typ, hdr_size, size = struct.unpack_from("<HHI", data, o)
        if size < 8 or o + size > len(data):
            raise ValueError("chunk overruns document")
        if typ == RES_XML_START_ELEMENT:
            name = pool[u32(data, o + 20)]
            attr_start, attr_size, attr_count = struct.unpack_from(
                "<HHH", data, o + 24
            )
            a0 = o + 16 + attr_start
            attrs = []
            for i in range(attr_count):
                a = a0 + i * attr_size
                if a + 20 > o + size:
                    raise ValueError("attribute overruns its chunk")
                aname = pool[u32(data, a + 4)]
                data_type = data[a + 15]
                dv = u32(data, a + 16)
                attrs.append((aname, attr_value(pool, data_type, dv), a))
            yield o, name, attrs, hdr_size, attr_start, attr_size, attr_count
        o += size
    if o != len(data):
        raise ValueError("trailing bytes after last chunk")


def should_drop(element, name, value):
    targets = DROP.get(element)
    if not targets or name not in targets:
        return False
    if name == "requiredSplitTypes":
        # Empty means "no required types" — the install is not gated.
        return bool(value)
    if name == "splitTypes":
        # Pairs with a non-empty requiredSplitTypes; removed together with it.
        return False
    if name == "isSplitRequired":
        return str(value).lower() == "true"
    return False


def strip(data):
    """Return (new_bytes_or_None, reason). None = nothing to change."""
    drops = []  # (chunk_offset, attr_array_offset, attr_size, count, element)
    for off, name, attrs, _hs, _astart, asize, _acount in walk(data):
        victims = [a for a in attrs if should_drop(name, a[0], a[1])]
        if not victims:
            continue
        # requiredSplitTypes governs the install; its companion splitTypes
        # (which declares types this APK provides) goes with it so no stale
        # pairing survives.
        if any(v[0] == "requiredSplitTypes" for v in victims) and name == "manifest":
            victims += [a for a in attrs if a[0] == "splitTypes"]
        drops.append((off, asize, victims, name))
    if not drops:
        return None, "clean"

    new = data
    # Apply bottom-up so earlier offsets stay valid. There is normally at most
    # one declaring element per manifest; the loop keeps the general case safe.
    for off, asize, victims, name in sorted(drops, key=lambda d: -d[0]):
        count = u16(new, off + 28)
        a0 = off + 16 + struct.unpack_from("<H", new, off + 24)[0]
        victim_positions = sorted((v[2] - a0) // asize for v in victims)
        keep = [
            new[a0 + i * asize : a0 + (i + 1) * asize]
            for i in range(count)
            if i not in set(victim_positions)
        ]
        removed = count - len(keep)
        if removed <= 0:
            return None, "parse drift"

        # id/class/style indices are 1-based positions into the attribute
        # array. We only ever remove requiredSplitTypes/splitTypes/
        # isSplitRequired, which are never one of those three, so an index can
        # only need shifting down by the number of removed records before it.
        idx = list(struct.unpack_from("<HHH", new, off + 30))
        for j in range(3):
            if idx[j]:
                idx[j] -= sum(1 for p in victim_positions if p < idx[j])

        body = b"".join(keep)
        start = a0 - off
        old_size = u32(new, off + 4)
        rebuilt = (
            new[: off + 4]
            + struct.pack("<I", old_size - removed * asize)
            + new[off + 8 : off + 28]
            + struct.pack("<H", count - removed)
            + struct.pack("<HHH", *idx)
            + new[off + 36 : off + start]
            + body
            + new[off + start + count * asize : off + old_size]
            + new[off + old_size :]
        )
        new = rebuilt

    # Document size bookkeeping: RES_XML wrapper size covers the whole file.
    total_delta = len(data) - len(new)
    if total_delta:
        wrapper_size = u32(new, 4)
        new = new[:4] + struct.pack("<I", wrapper_size - total_delta) + new[8:]
        if u32(new, 4) != len(new):
            return None, "size mismatch"

    # Self-check: re-parse, ensure the gates are gone, structure is intact.
    for off, name, attrs, *_ in walk(new):
        for aname, aval, _ in attrs:
            if aname in ("requiredSplitTypes", "splitTypes", "isSplitRequired") and (
                aname != "isSplitRequired" or str(aval).lower() == "true"
            ):
                if name in DROP and aname in DROP[name]:
                    return None, "attribute survived rewrite"
    return new, "stripped"


def fix_apk(path):
    """APK mode: analyse and rewrite the archive in place. Returns exit code."""
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        if "AndroidManifest.xml" not in {i.filename for i in infos}:
            print(f"{path}: no AndroidManifest.xml; leaving untouched",
                  file=sys.stderr)
            return 0
        try:
            new, reason = strip(z.read("AndroidManifest.xml"))
        except Exception as exc:  # odd manifest: not ours to fix
            print(f"{path}: cannot analyse manifest ({exc}); leaving untouched",
                  file=sys.stderr)
            return 0
        if new is None:
            if reason == "clean":
                return 0
            print(f"{path}: split-type strip failed: {reason}", file=sys.stderr)
            return 1
        tmp = path + ".striptmp"
        try:
            comment = z.comment
            with zipfile.ZipFile(tmp, "w") as out:
                out.comment = comment
                for info in infos:
                    # Drop the data-descriptor flag: sizes are known upfront.
                    info.flag_bits &= ~0x08
                    with z.open(info) as fh:
                        payload = fh.read()
                    if info.filename == "AndroidManifest.xml":
                        payload = new
                    out.writestr(info, payload)
            os.replace(tmp, path)
        except Exception as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            print(f"{path}: could not rewrite apk ({exc}); leaving untouched",
                  file=sys.stderr)
            return 1
    print("removed Android 12+ split-type requirement from manifest")
    return 10


def main(argv):
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 1
    path = argv[1]
    if zipfile.is_zipfile(path):
        return fix_apk(path)
    try:
        with open(path, "rb") as f:
            data = f.read()
        new, reason = strip(data)
    except Exception as exc:  # unreadable/odd manifest: not our problem to fix
        print(f"{path}: cannot analyse manifest ({exc}); leaving untouched",
              file=sys.stderr)
        return 0
    if new is None:
        if reason == "clean":
            return 0
        print(f"{path}: split-type strip failed: {reason}", file=sys.stderr)
        return 1
    tmp = path + ".stripped.tmp"
    with open(tmp, "wb") as f:
        f.write(new)
    os.replace(tmp, path)
    print("removed Android 12+ split-type requirement from manifest")
    return 10


if __name__ == "__main__":
    sys.exit(main(sys.argv))
