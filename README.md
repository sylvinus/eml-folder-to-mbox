# eml-folder-to-mbox

Convert a `.tgz` archive containing a folder tree of `.eml`, `.ics` and `.vcf`
files into **three condensed files** ready to upload anywhere:

- `mail.mbox` — every email concatenated as **mboxrd**, with the originating
  folder path preserved as a label (`X-Keywords`) and `Sent` / `Drafts`
  folders mapped to mbox status flags;
- `contacts.vcf` — every contact concatenated (the vCard spec allows
  multiple `BEGIN:VCARD…END:VCARD` blocks in a single file);
- `calendar.ics` — every calendar component merged into one `VCALENDAR`
  (with `VTIMEZONE` entries deduplicated by `TZID`);
- `others/` — anything else found in the archive (PDFs, images, …).

The mbox output follows the format documented at
[suitenumerique/messages — `docs/mbox.md`](https://github.com/suitenumerique/messages/blob/main/docs/mbox.md):
mboxrd variant, with `Status` / `X-Status` / `X-Keywords` headers, compatible
with Thunderbird, Apple Mail, mu4e, Dovecot, OfflineIMAP, Google Takeout and
the Messages importer.

## Input archive

The archive format this tool targets is a **Zimbra mailbox export** —
produced by Zimbra Collaboration Suite (and forks like Zextras / Carbonio)
when an administrator or user requests a full-mailbox export. Tell-tale
signs include:

- top-level mail folders (`Inbox`, `Sent`, `Drafts`, …) with `.eml` files
  named like `0000017641-<subject>.eml`;
- duplicate folder names disambiguated by a `!1`, `!2`, … suffix
  (e.g. `Sent`, `Sent!1`, `Sent!2`);
- a `Briefcase/` folder containing the user's uploaded files;
- a `Calendar/` folder of `.ics` files;
- `Contacts/` and `Emailed Contacts/` folders of `.vcf` files;
- `Message-ID` headers ending in `@…JavaMail.zimbra@…`.

The tool nevertheless makes no Zimbra-specific assumptions beyond those
folder/file naming conventions — any archive that uses one `.eml` / `.vcf` /
`.ics` per file inside folders will convert correctly.


## Usage

Requires Python 3.9+ (standard library only, no dependencies).

```bash
python3 eml_folder_to_mbox.py path/to/archive.tgz
```

Pass `-v` to print progress.

Output always lands in `./output/`. The archive is streamed — nothing is fully
extracted first, so memory stays low even for multi-gigabyte exports.

```
output/
├── mail.mbox          # all emails (mboxrd)
├── contacts.vcf       # all contacts concatenated
├── calendar.ics       # all calendar items in one VCALENDAR
└── others/            # everything else (PDFs, images, …)
```


## Format details

### Folder path → label

Every email gets an `X-Keywords` header whose value is the path of the folder
that contained the `.eml` file inside the archive. Subfolders are kept with
`/` as the separator, and labels containing spaces, commas or quotes are
quoted per the spec:

| Archive path | `X-Keywords` value |
|---|---|
| `Inbox/foo.eml` | `Inbox` |
| `Inbox/ARCHIVE PROJETS/foo.eml` | `"Inbox/ARCHIVE PROJETS"` |
| `Comptabilité/Banques/foo.eml` | `Comptabilité/Banques` |
| `Ressources Humaines/foo.eml` | `"Ressources Humaines"` |

Zimbra deduplicates folders with the same name by appending `!1`, `!2`, … to
the directory name. The script strips those suffixes so all variants collapse
onto the original label:

| Archive path | `X-Keywords` value |
|---|---|
| `Sent/foo.eml` | `Sent` |
| `Sent!1/foo.eml` | `Sent` |
| `Sent!2/foo.eml` | `Sent` |

### Special folders → flags

The top-level folder is also matched (case-insensitively) against a few
well-known names, and the corresponding mbox flag is added to the `X-Status`
header. The original label is kept too, so importers that key off labels and
importers that key off flags both work.

| Top-level folder name | `X-Status` flag | Meaning |
|---|---|---|
| `Sent`, `Outbox`, `Sent Items`, `Sent Mail` | `A` | sent / answered |
| `Drafts`, `Draft` | `T` | draft |

Every message also gets `Status: O` (old / non-recent) — the convention
expected for mbox exports.

### mboxrd specifics

- Messages are separated by a `From ` envelope line at column 0, preceded by
  a blank line.
- Any body line matching `^>*From ` is prefixed with an additional `>` to
  preserve round-trip integrity.
- The envelope line uses the `Return-Path` (or `From`) address as the
  sender and the `Date` header as the timestamp (falling back to the file
  mtime).
- Existing `Status`, `X-Status`, `X-Keywords` and `X-Gmail-Labels` headers
  on the incoming message are stripped before our own are injected, so the
  output is canonical.

### Concatenated `.vcf`

Each input `.vcf` block is appended as-is to a single file. The vCard standard
allows multiple `BEGIN:VCARD…END:VCARD` blocks per file, so it can be imported
into Google Contacts, Apple Contacts, Thunderbird, etc. as one file.

### Merged `.ics`

All input `.ics` files are parsed, unfolded (RFC 5545 line-continuation), and
their top-level components (`VEVENT`, `VTODO`, `VTIMEZONE`, …) are placed
inside a single new `BEGIN:VCALENDAR / END:VCALENDAR` wrapper. `VTIMEZONE`
components are deduplicated by `TZID`, since most calendar entries from the
same source re-emit the same zone definitions.


## Example output

Headers injected by the script for an email originally located at
`Sent!1/0000003020-Compte rendu de reunion.eml`:

```
Status: O
X-Status: A
X-Keywords: Sent
```

And for `Inbox/ARCHIVE PROJETS/0000017149-Fwd_ Demande de devis.eml`:

```
Status: O
X-Keywords: "Inbox/ARCHIVE PROJETS"
```


## Verifying the result

The output mbox can be opened with anything that understands mboxrd, e.g.:

```python
import mailbox
mbox = mailbox.mbox("output/mail.mbox")
print(len(mbox), "messages")
for msg in mbox:
    print(msg["Date"], "|", msg["X-Keywords"], "|", msg["Subject"])
```

Or imported directly into Thunderbird via the
[ImportExportTools NG](https://addons.thunderbird.net/addon/importexporttools-ng/)
add-on (`Tools → ImportExportTools NG → Import mbox file`).


## License

MIT — see `LICENSE`.
