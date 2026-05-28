from scripts.crawlers.property_manual_crawler import (
    PROPERTY_MANUAL_URL,
    html_to_manual_markdown,
)


def test_html_to_manual_markdown_adds_metadata_and_removes_junk():
    html = """
    <html>
      <body>
        <nav>Search Previous Next</nav>
        <main>
          <div class="breadcrumbs">Manuals / Property</div>
          <p>o Appetite</p>
          <p>o Triple Net Lease</p>
          <h1>Property Manual</h1>
          <h2>Triple Net Lease</h2>
          <p>Buildings with a triple net lease should be referred to your Coaction underwriter.</p>
          <button>Copy link</button>
          <h2>Optional Coverages</h2>
          <table>
            <tr>
              <th>Coverage Option</th>
              <th>Form Number(s)</th>
            </tr>
            <tr>
              <td>Spoilage Coverage</td>
              <td>CP 04 40 12 20</td>
            </tr>
          </table>
          <p>AI-generated content may be incorrect.</p>
        </main>
        <footer>Was this helpful?</footer>
      </body>
    </html>
    """

    markdown = html_to_manual_markdown(html)

    assert markdown.startswith(f"SOURCE_URL: {PROPERTY_MANUAL_URL}\nMANUAL_TYPE: Property\n---\n\n")
    assert "# Property Manual" in markdown
    assert "## Triple Net Lease" in markdown
    assert "Buildings with a triple net lease" in markdown
    assert "| Coverage Option | Form Number(s) |" in markdown
    assert "| --- | --- |" in markdown
    assert "| Spoilage Coverage | CP 04 40 12 20 |" in markdown
    assert "Search Previous Next" not in markdown
    assert "o Appetite" not in markdown
    assert "AI-generated content may be incorrect" not in markdown
    assert "Was this helpful" not in markdown
