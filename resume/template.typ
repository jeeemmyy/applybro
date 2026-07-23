// =========================================================================
// template.typ — LAYOUT ONLY.  Reads content from content.yaml and renders
// the resume.  No resume wording lives in this file; changing content.yaml
// must never change the visual layout.  This is a faithful, pixel-level
// recreation of the original jsPDF-generated resume (all Helvetica, US Letter).
//
// Baseline model (measured from the original):
//     baseline_gap = space + 0.7173 * fontsize
// holds for both intra-paragraph leading and inter-paragraph spacing.  All the
// vertical constants below were tuned by numerically comparing glyph and rule
// positions against the original PDF.  Change layout ONLY here, never in
// content.yaml.
// =========================================================================

// Content file is "content.yaml" by default, but can be overridden for tailored
// variants without touching the master, e.g.:
//   typst compile --input content=content.acme.yaml template.typ acme.pdf
#let content-path = sys.inputs.at("content", default: "content.yaml")
#let cfg = yaml(content-path)

// ---- Colors (exact hex sampled from the original) -----------------------
#let c-text   = rgb("#111111")   // body text
#let c-head   = rgb("#1f4e79")   // section headers, company & role names
#let c-link   = rgb("#0b63b6")   // links (email / phone / urls)
#let c-italic = rgb("#333333")   // italic company sub-line
#let c-date   = rgb("#555555")   // role date ranges
#let c-rule   = rgb("#9fb7c9")   // header underline rules & box borders
#let c-boxfl  = rgb("#eaf2f8")   // shaded company-header box fill

// ---- Geometry -----------------------------------------------------------
// Page margin is 36pt (0.5in) L/R; section content is inset a further 6pt so
// text runs x=42..570, matching the original.  RPAD keeps the right edge at
// 570 regardless of the left indent so line wrapping matches the original.
#let RPAD = 6pt

// ---- Baseline model:  baseline_gap = space + LH * fontsize ---------------
#let LH = 0.7173
#let leadfor(gap, size) = gap - LH * size

// ---- Inline **bold** parser for content strings -------------------------
#let rich(s) = {
  let parts = str(s).split("**")
  for (i, part) in parts.enumerate() {
    if calc.odd(i) { strong(part) } else { part }
  }
}

#let romans = ("i","ii","iii","iv","v","vi","vii","viii","ix","x","xi","xii")

// ---- A text block: controlled size, baseline gap, and indents -----------
#let blk(size: 8pt, gap: 9pt, indent: 0pt, rindent: RPAD, hang: 0pt,
         fill: c-text, body) = {
  pad(left: indent, right: rindent, {
    set text(size: size, fill: fill)
    set par(leading: leadfor(gap, size), spacing: leadfor(gap, size),
            hanging-indent: hang, justify: false)
    body
  })
}

// ---- Bullet list: literal "- " prefix, hanging indent aligns wraps ------
#let bullets(items, size: 7.6pt, gap: 8.5pt, indent: 9pt, hang: 5pt) = {
  blk(size: size, gap: gap, indent: indent, hang: hang, {
    // Each bullet is its own explicit paragraph so the hanging indent applies
    // uniformly (a bare first paragraph in a block otherwise skips the hang).
    for it in items {
      par(hanging-indent: hang, "- " + rich(it))
    }
  })
}

// ---- Section header: navy title + light-blue underline rule -------------
#let section(title) = {
  blk(size: 11.2pt, gap: 11.2pt, indent: 6pt, fill: c-head, strong(upper(title)))
  v(5.6pt, weak: false)                       // header -> rule
  pad(left: 6pt, line(length: 528pt, stroke: 0.7pt + c-rule))
}

// ---- One contact item (blue if it is a link) ----------------------------
#let contact-item(it) = {
  let col = if it.at("link", default: false) { c-link } else { c-text }
  // box() keeps each item unbreakable so wrapping only happens at " | "
  // separators — long URLs stay intact and wrap as a whole, like the original.
  let body = box(text(fill: col, it.text))
  if "href" in it { link(it.href, body) } else { body }
}

#set page(
  paper: "us-letter",
  margin: (top: 34pt, left: 36pt, right: 36pt, bottom: 28pt),
)
#set text(font: "Helvetica", fill: c-text, size: 8pt)
#set par(leading: 4pt, spacing: 0pt, justify: false)

// =========================== HEADER ======================================
// Circular photo, top-right, out of flow. OPTIONAL (SaaS Phase 2): resumes
// parsed from an uploaded PDF have no photo — omitting contact.photo (or
// setting it null) skips it without shifting anything else.
#if cfg.contact.at("photo", default: none) != none {
  place(top + right, dx: 0pt, dy: 3.4pt,
    image(cfg.contact.photo, width: 87.8pt))
}

// Name / title / contact — width-constrained so the contact line wraps
// (github -> line 2) exactly as the original, clearing the photo.
#box(width: 452pt, {
  blk(size: 22pt, gap: 22pt, indent: 0pt, rindent: 0pt, strong(cfg.contact.name))
  v(8.0pt, weak: false)
  blk(size: 11.5pt, gap: 11.5pt, indent: 0pt, rindent: 0pt, strong(cfg.contact.title))
  v(7.1pt, weak: false)
  blk(size: 7.9pt, gap: 9.0pt, indent: 0pt, rindent: 0pt, {
    let items = cfg.contact.info.map(contact-item)
    items.join(text(fill: c-text)[ | ])   // literal ASCII pipe separator
  })
})

// Sections below are OPTIONAL (SaaS Phase 2): resumes parsed from uploaded
// PDFs may lack a summary, skill groups, or a featured project — a missing
// key skips the whole section. Zaeem's own content has all of them, so his
// output is unchanged.

// =========================== SUMMARY =====================================
#if cfg.at("summary", default: none) != none {
  v(50.7pt, weak: false)
  section("Professional Summary")
  v(5.1pt, weak: false)
  blk(size: 7.9pt, gap: 9.0pt, indent: 6pt, rich(cfg.summary))
}

// =========================== CORE SKILLS =================================
#if cfg.at("skills", default: none) != none {
  v(18.3pt, weak: false)
  section("Core Skills")
  v(5.1pt, weak: false)
  blk(size: 7.8pt, gap: 9.78pt, indent: 6pt, {
    for (i, sk) in cfg.skills.enumerate() {
      if i > 0 { parbreak() }
      strong(sk.label + ":")
      [ ]
      sk.text
    }
  })
}

// =========================== FEATURED PROJECT ============================
// The title's link is also optional — no URL renders a plain title.
#if cfg.at("featured", default: none) != none {
  v(22.1pt, weak: false)
  section("Featured AI Project")
  v(5.1pt, weak: false)
  blk(size: 8.4pt, gap: 8.4pt, indent: 6pt, {
    let flink = cfg.featured.at("link", default: none)
    if flink != none {
      strong(cfg.featured.title + " | ")
      strong(link(cfg.featured.at("href", default: flink),
        text(fill: c-link, flink)))
    } else {
      strong(cfg.featured.title)
    }
  })
  v(5.3pt, weak: false)
  bullets(cfg.featured.bullets, size: 7.6pt, gap: 8.5pt, indent: 9pt, hang: 5pt)
}

// =========================== EXPERIENCE =================================
#v(23.6pt, weak: false)
#section("Professional Experience")

#for (ci, co) in cfg.experience.enumerate() {
  // Gap above the company box: small after the section rule (first company),
  // larger between companies.
  if ci == 0 { v(5.1pt, weak: false) } else { v(8.0pt, weak: false) }
  // Full-width shaded company header box.
  block(width: 100%, fill: c-boxfl, stroke: 0.5pt + c-rule,
        inset: (left: 5pt, right: 5pt, top: 2.3pt, bottom: 7.2pt), {
    set text(size: 8.8pt)
    strong([#(ci + 1)- ] + text(fill: c-head, co.company) + " | " + co.meta)
  })
  // Optional italic company sub-line.
  if "summary" in co {
    v(-0.6pt, weak: false)
    blk(size: 7.6pt, gap: 8.6pt, indent: 6pt, fill: c-italic, emph(co.summary))
  }
  // Roles.
  let ri = 0
  for role in co.roles {
    if "dates" in role {
      // promotion-track style: navy roman-numeral heading + grey dates
      v(4.8pt, weak: false)
      blk(size: 8.4pt, gap: 8.4pt, indent: 40pt, {
        strong(text(fill: c-head, romans.at(ri) + "- " + role.title + " "))
        strong(text(fill: c-date, "| " + role.dates))
      })
      v(3.9pt, weak: false)
      bullets(role.bullets, size: 7.4pt, gap: 8.5pt, indent: 54pt, hang: 5pt)
      ri = ri + 1
    } else {
      // plain sub-heading style (e.g. internship)
      v(0.5pt, weak: false)
      blk(size: 8.2pt, gap: 8.2pt, indent: 24pt, strong(role.title))
      v(3.0pt, weak: false)
      bullets(role.bullets, size: 7.4pt, gap: 8.5pt, indent: 36pt, hang: 5pt)
    }
  }
}

// =========================== EDUCATION =================================
// Whole section + each sub-part optional (parsed resumes vary).
#if cfg.at("education", default: none) != none {
  v(21.7pt, weak: false)
  section("Education & Certifications")
  v(5.1pt, weak: false)
  blk(size: 7.7pt, gap: 9.6pt, indent: 6pt, {
    for (i, d) in cfg.education.at("degrees", default: ()).enumerate() {
      if i > 0 { parbreak() }
      strong(d.degree)
      [ | ]
      d.meta
    }
    let certs = cfg.education.at("certifications", default: none)
    if certs != none {
      parbreak()
      strong("Certifications:")
      [ ]
      certs
    }
  })
  let notes = cfg.education.at("notes", default: none)
  if notes != none {
    v(4.1pt, weak: false)
    bullets(notes, size: 7.6pt, gap: 8.4pt, indent: 11pt, hang: 5pt)
  }
}
