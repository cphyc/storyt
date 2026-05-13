from playwright.sync_api import Page, expect


def test_simulations_render(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    expect(page.locator("text=sim_a")).to_be_visible()
    expect(page.locator("text=sim_b")).to_be_visible()


def test_click_sim_a_shows_outputs(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    expect(page.locator("text=iout=00001").first).to_be_visible()
    expect(page.locator("text=iout=00002").first).to_be_visible()


def test_output_card_shows_sibling(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    expect(page.locator("text=halo_catalogue").first).to_be_visible()


def test_expand_shows_mass_value(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    # Expand the first output card (iout=00001 → halo_catalogue id=30 → mass=42)
    # mass.json is at data/sim_a/mass.json (parent dir of "sim_a/halos_00001.txt")
    page.locator("[data-testid='expand-btn']").first.click()
    expect(page.locator("text=42")).to_be_visible()


def test_no_data_shows_na(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    # Expand the second output card (halo_catalogue id=31 has no entry → N/A)
    page.locator("[data-testid='expand-btn']").nth(1).click()
    expect(page.locator("text=N/A")).to_be_visible()


def test_no_refetch_on_reexpand(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")

    requests = []
    page.on(
        "request", lambda r: requests.append(r.url) if "mass.json" in r.url else None
    )

    expand_btn = page.locator("[data-testid='expand-btn']").first
    expand_btn.click()
    page.wait_for_timeout(500)
    expand_btn.click()  # collapse
    page.wait_for_timeout(200)
    expand_btn.click()  # re-expand
    page.wait_for_timeout(500)

    mass_requests = [r for r in requests if "mass.json" in r]
    assert len(mass_requests) == 1, (
        f"Expected 1 fetch, got {len(mass_requests)}: {mass_requests}"
    )


def test_breadcrumb_navigation(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    # Breadcrumb should show sim_a
    expect(page.locator("[data-testid='breadcrumb']")).to_contain_text("sim_a")
    # Click "Home" (first span in breadcrumb) to go back
    page.locator("[data-testid='breadcrumb'] span").first.click()
    # Should show both sims again
    expect(page.locator("text=sim_b")).to_be_visible()


# ---------------------------------------------------------------------------
# URL hash navigation tests
# ---------------------------------------------------------------------------


def test_url_hash_loads_simulation_level(page: Page, http_server):
    """Navigating directly to /#sim_a should restore the simulation level."""
    page.goto(f"{http_server}/index.html#sim_a")
    # Should show output cards for sim_a without clicking
    expect(page.locator("text=iout=00001").first).to_be_visible()
    expect(page.locator("text=iout=00002").first).to_be_visible()


def test_url_hash_breadcrumb_after_direct_link(page: Page, http_server):
    """Breadcrumb correctly shows the path when loaded from a URL hash."""
    page.goto(f"{http_server}/index.html#sim_a")
    crumb = page.locator("[data-testid='breadcrumb']")
    expect(crumb).to_contain_text("sim_a")


def test_url_hash_loads_halo_catalogue_level(page: Page, http_server):
    """Navigating to /#sim_a|sim_a/halos_00001.txt restores the halo listing.

    This exercises the pattern-based hashToStack: the url_path
    'sim_a/halos_00001.txt' must be matched against the halo_catalogue pattern
    'halos_(?P<iout>\\d{5})\\.txt' to select the correct tree node.
    """
    page.goto(f"{http_server}/index.html#sim_a|sim_a/halos_00001.txt")
    # halo listing should be shown directly
    expect(page.locator("text=halo_id=0001").first).to_be_visible()
    expect(page.locator("text=halo_id=0002").first).to_be_visible()


def test_url_hash_two_level_breadcrumb(page: Page, http_server):
    """Two-level hash produces a breadcrumb with both segments."""
    page.goto(f"{http_server}/index.html#sim_a|sim_a/halos_00001.txt")
    crumb = page.locator("[data-testid='breadcrumb']")
    expect(crumb).to_contain_text("sim_a")
    expect(crumb).to_contain_text("halos_00001.txt")


def test_url_hash_back_button(page: Page, http_server):
    """Browser back button returns to the previous level."""
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    page.wait_for_timeout(300)
    page.go_back()
    page.wait_for_timeout(300)
    # Should show the root simulation list again
    expect(page.locator("text=sim_b")).to_be_visible()


def test_sibling_property_fetched_from_parent_dir(page: Page, http_server):
    """Regression: sibling property files live at data/<parent>/<prop>.json,
    NOT data/<sibling_url_path>/<prop>.json.

    Verifies the fix in InstanceCard.tsx that strips the last path segment from
    sib.url_path before constructing the fetch URL.
    """
    fetched_urls: list[str] = []
    page.on("request", lambda r: fetched_urls.append(r.url))

    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    page.locator("[data-testid='expand-btn']").first.click()
    expect(page.locator("text=42")).to_be_visible()

    mass_urls = [u for u in fetched_urls if "mass.json" in u]
    assert mass_urls, "mass.json was never fetched"
    # Must be data/sim_a/mass.json (parent dir), not data/sim_a/halos_00001.txt/mass.json
    assert all("/sim_a/mass.json" in u for u in mass_urls), (
        f"Expected fetch from sim_a/mass.json but got: {mass_urls}"
    )
