#!/usr/bin/env python3
"""
eml-folder-to-mbox
==================

Convert a `.tgz` archive containing a folder tree of `.eml`, `.ics` and `.vcf`
files into three condensed files plus an `others/` directory:

  * `mail.mbox`    — every email as mboxrd, folder path stored as X-Keywords,
                     Sent/Drafts folders mapped to mbox status flags.
  * `contacts.vcf` — every vCard concatenated.
  * `calendar.ics` — every calendar component merged into one VCALENDAR.
  * `others/`      — anything else found in the archive.

mbox format: https://github.com/suitenumerique/messages/blob/main/docs/mbox.md
"""

from __future__ import annotations

import argparse
import email.utils
import os
import re
import shutil
import sys
import tarfile
import time
from collections import Counter
from email.parser import BytesHeaderParser


# Zimbra deduplicates folders with `!1`, `!2`, … — strip those.
_DUP_RE = re.compile(r"!\d+$")
_MBOXRD_FROM_RE = re.compile(rb"(?m)^(>*From )")
_META_HDRS = (b"status", b"x-status", b"x-keywords", b"x-gmail-labels")


def normalize_label(parts):
    return "/".join(_DUP_RE.sub("", p) for p in parts if p)


def quote_label(label):
    if any(c in label for c in (",", " ", "\t", '"')):
        return '"' + label.replace('"', "") + '"'
    return label


def split_headers_body(data):
    """Return (headers, newline, body) given raw RFC822 bytes."""
    for sep, nl in ((b"\r\n\r\n", b"\r\n"), (b"\n\n", b"\n")):
        i = data.find(sep)
        if i >= 0:
            return data[:i], nl, data[i + len(sep):]
    return data, b"\n", b""


def strip_meta_headers(headers, nl):
    out, skipping = [], False
    for line in headers.split(nl):
        if line[:1] in (b" ", b"\t"):
            if not skipping:
                out.append(line)
            continue
        if b":" in line and line.split(b":", 1)[0].strip().lower() in _META_HDRS:
            skipping = True
            continue
        skipping = False
        out.append(line)
    return nl.join(out)


def envelope_line(headers, fallback_epoch):
    """Build the `From <sender> <date>` mbox separator."""
    msg = BytesHeaderParser().parsebytes(headers)
    addr = email.utils.parseaddr(msg.get("Return-Path") or msg.get("From") or "")[1]
    sender = addr.split()[0] if addr else "MAILER-DAEMON"
    when = None
    if msg.get("Date"):
        try:
            when = email.utils.parsedate_to_datetime(msg["Date"]).timetuple()
        except Exception:
            pass
    if when is None:
        when = time.gmtime(fallback_epoch)
    return f"From {sender} {time.strftime('%a %b %d %H:%M:%S %Y', when)}"


def write_mbox_message(out, raw, label, fallback_epoch):
    headers, nl, body = split_headers_body(raw)
    headers = strip_meta_headers(headers, nl)

    extras = [b"Status: O"]
    top = label.split("/", 1)[0].lower() if label else ""
    if top == "sent":
        extras.append(b"X-Status: A")
    elif top in ("drafts", "draft"):
        extras.append(b"X-Status: T")
    if label:
        extras.append(b"X-Keywords: " + quote_label(label).encode("utf-8"))

    if headers and not headers.endswith(nl):
        headers += nl
    headers += nl.join(extras) + nl

    out.write(envelope_line(headers, fallback_epoch).encode("utf-8") + b"\n")
    out.write(headers)
    out.write(nl)
    out.write(_MBOXRD_FROM_RE.sub(rb">\1", body))
    if not body.endswith(b"\n"):
        out.write(b"\n")
    out.write(b"\n")


def ical_components(data):
    """Yield (component_name, [lines]) for top-level components in one .ics file."""
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Unfold RFC 5545 continuation lines.
    lines = []
    for ln in text.split("\n"):
        if ln[:1] in (" ", "\t") and lines:
            lines[-1] += ln[1:]
        else:
            lines.append(ln)
    in_cal, current, body = False, None, []
    for ln in lines:
        s = ln.rstrip()
        if not s:
            continue
        u = s.upper()
        if u == "BEGIN:VCALENDAR":
            in_cal = True
        elif u == "END:VCALENDAR":
            in_cal = False
        elif in_cal and current is None and u.startswith("BEGIN:"):
            current, body = s.split(":", 1)[1].strip().upper(), [s]
        elif current is not None:
            body.append(s)
            if u == f"END:{current}":
                yield current, body
                current, body = None, []


def convert(archive_path, out_dir, verbose=False):
    os.makedirs(out_dir, exist_ok=True)
    others_dir = os.path.join(out_dir, "others")

    msg_count = cal_count = vcf_count = others_count = 0
    labels = Counter()
    seen_tz = set()
    timezones, events, other_comps = [], [], []

    mbox_path = os.path.join(out_dir, "mail.mbox")
    vcf_path = os.path.join(out_dir, "contacts.vcf")
    ics_path = os.path.join(out_dir, "calendar.ics")

    with open(mbox_path, "wb") as mbox_fh, open(vcf_path, "wb") as vcf_fh, \
            tarfile.open(archive_path, mode="r|*") as tar:
        for member in tar:
            if not member.isfile():
                continue
            parts = [p for p in member.name.split("/") if p and p != "."]
            if not parts:
                continue
            ext = os.path.splitext(parts[-1])[1].lower()
            label = normalize_label(parts[:-1])
            stream = tar.extractfile(member)
            if stream is None:
                continue

            if ext == ".eml":
                write_mbox_message(mbox_fh, stream.read(), label, member.mtime)
                msg_count += 1
                labels[label] += 1
                if verbose and msg_count % 200 == 0:
                    print(f"  ... {msg_count} messages", file=sys.stderr)

            elif ext == ".vcf":
                data = stream.read()
                if data and not data.endswith((b"\n", b"\r")):
                    data += b"\r\n"
                vcf_fh.write(data)
                vcf_count += 1

            elif ext == ".ics":
                for name, body in ical_components(stream.read()):
                    if name == "VTIMEZONE":
                        tzid = next(
                            (ln.split(":", 1)[-1].strip()
                             for ln in body if ln.upper().startswith("TZID")),
                            "",
                        )
                        if tzid and tzid in seen_tz:
                            continue
                        if tzid:
                            seen_tz.add(tzid)
                        timezones.append(body)
                    elif name == "VEVENT":
                        events.append(body)
                    else:
                        other_comps.append(body)
                cal_count += 1

            else:
                os.makedirs(others_dir, exist_ok=True)
                with open(os.path.join(others_dir, parts[-1].replace("/", "_")), "wb") as fh:
                    shutil.copyfileobj(stream, fh)
                others_count += 1

    with open(ics_path, "wb") as ics_fh:
        out_lines = ["BEGIN:VCALENDAR", "PRODID:-//eml-folder-to-mbox//EN", "VERSION:2.0"]
        for comp in timezones + events + other_comps:
            out_lines.extend(comp)
        out_lines.append("END:VCALENDAR")
        ics_fh.write(("\r\n".join(out_lines) + "\r\n").encode("utf-8"))

    return msg_count, vcf_count, cal_count, others_count, labels


def main():
    parser = argparse.ArgumentParser(
        description="Convert a .tgz of .eml/.ics/.vcf files into mail.mbox + "
                    "contacts.vcf + calendar.ics (plus an others/ folder)."
    )
    parser.add_argument("archive", help="Input .tgz archive path")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not os.path.isfile(args.archive):
        sys.exit(f"error: archive not found: {args.archive}")

    msgs, vcfs, ics, others, labels = convert(args.archive, "output", args.verbose)

    print("Done. Output: output/")
    print(f"  messages : {msgs:>5}  -> mail.mbox")
    print(f"  contacts : {vcfs:>5}  -> contacts.vcf")
    print(f"  calendar : {ics:>5}  -> calendar.ics")
    print(f"  others   : {others:>5}  -> others/")
    if labels:
        print("  labels:")
        for label, count in labels.most_common():
            print(f"    {count:>5}  {label or '(no label)'}")


if __name__ == "__main__":
    main()
