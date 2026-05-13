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
    # Expand the first output card (iout=00001 → mass=42)
    page.locator("[data-testid='expand-btn']").first.click()
    expect(page.locator("text=42")).to_be_visible()


def test_no_data_shows_na(page: Page, http_server):
    page.goto(f"{http_server}/index.html")
    page.click("text=sim_a")
    # Expand the second output card (no mass.json for halos_00002.txt → N/A)
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
