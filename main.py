import os
import re
import uuid
import shutil
from decimal import Decimal, InvalidOperation
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from playwright.async_api import async_playwright


os.environ["PLAYWRIGHT_BROWSERS_PATH"] = "/ms-playwright"
os.environ["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"


BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
REPORTS_DIR = BASE_DIR / "reports"

REPORTS_DIR.mkdir(exist_ok=True)

app = FastAPI()

if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

app.mount("/reports", StaticFiles(directory=REPORTS_DIR), name="reports")

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


VALID_STATUSES = {"urgent", "recommended", "optional"}


def slugify(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value).strip("_")
    return value or "property"


def format_currency(amount: Decimal) -> str:
    return f"${amount:,.2f}"


def parse_money(value: str) -> Decimal:
    cleaned = (value or "").replace("$", "").replace(",", "").strip()
    if not cleaned:
        raise InvalidOperation("Price is blank")
    return Decimal(cleaned)


def safe_text(value: str) -> str:
    return (value or "").strip()


def error_page(message: str) -> HTMLResponse:
    return HTMLResponse(
        content=f"""
        <!doctype html>
        <html lang="en">
        <head>
          <meta charset="utf-8">
          <title>Validation Error</title>
          <style>
            body {{
              font-family: Arial, Helvetica, sans-serif;
              background: #F7F9FB;
              margin: 0;
              padding: 40px 20px;
              color: #1F2933;
            }}
            .card {{
              max-width: 760px;
              margin: 0 auto;
              background: #FFFFFF;
              border: 1px solid #E3E7ED;
              border-radius: 12px;
              padding: 24px;
            }}
            h1 {{
              margin: 0 0 12px 0;
              font-size: 24px;
            }}
            p {{
              margin: 0 0 16px 0;
              line-height: 1.5;
              color: #6B7280;
            }}
            a {{
              display: inline-block;
              background: #2F6FED;
              color: white;
              text-decoration: none;
              padding: 12px 16px;
              border-radius: 8px;
              font-weight: 700;
            }}
          </style>
        </head>
        <body>
          <div class="card">
            <h1>please fix the form</h1>
            <p>{message}</p>
            <a href="/">go back</a>
          </div>
        </body>
        </html>
        """,
        status_code=400,
    )


@app.get("/", response_class=HTMLResponse)
async def form(request: Request):
    return templates.TemplateResponse("form.html", {"request": request})


@app.post("/generate")
async def generate_report(request: Request):
    form = await request.form()

    property_address = safe_text(form.get("property_address"))
    agency = safe_text(form.get("agency"))
    requested_by = safe_text(form.get("requested_by"))
    work_order = safe_text(form.get("work_order"))
    inspection_date = safe_text(form.get("inspection_date"))
    priority = safe_text(form.get("priority"))
    client_name = safe_text(form.get("client_name"))
    tenant_name = safe_text(form.get("tenant_name"))
    job_number = safe_text(form.get("job_number"))
    estimated_timeframe = safe_text(form.get("estimated_timeframe"))

    company_name = safe_text(form.get("company_name"))
    company_phone = safe_text(form.get("company_phone"))
    company_email = safe_text(form.get("company_email"))
    company_website = safe_text(form.get("company_website"))
    company_abn = safe_text(form.get("company_abn"))

    if not property_address:
        return error_page("property address is required.")
    if not agency:
        return error_page("agency is required.")
    if not inspection_date:
        return error_page("inspection date is required.")
    if not estimated_timeframe:
        return error_page("estimated timeframe is required.")

    try:
        issue_count = int(form.get("issue_count", "0"))
    except ValueError:
        issue_count = 0

    if issue_count < 1:
        return error_page("add at least one issue before generating the report.")

    report_reference = f"IR-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    report_dir = REPORTS_DIR / report_reference
    report_dir.mkdir(exist_ok=True)

    issues = []
    included_quote_items = []
    optional_quote_items = []

    urgent_count = 0
    recommended_count = 0
    optional_count = 0

    subtotal = Decimal("0.00")
    optional_subtotal = Decimal("0.00")

    for i in range(issue_count):
        title = safe_text(form.get(f"issue_{i}_title"))
        location = safe_text(form.get(f"issue_{i}_location"))
        status = safe_text(form.get(f"issue_{i}_status")).lower()
        observed_condition = safe_text(form.get(f"issue_{i}_observed_condition"))
        recommended_action = safe_text(form.get(f"issue_{i}_recommended_action"))
        price_raw = safe_text(form.get(f"issue_{i}_price"))
        pricing_note = safe_text(form.get(f"issue_{i}_pricing_note")) or "Includes labour and materials"
        include_in_quote = safe_text(form.get(f"issue_{i}_include_in_quote")).lower() == "yes"
        is_optional_quote_item = safe_text(form.get(f"issue_{i}_optional")).lower() == "yes"

        scope_items = [
            safe_text(item)
            for item in form.getlist(f"issue_{i}_scope")
            if safe_text(item)
        ]

        uploads = form.getlist(f"issue_{i}_photos")

        if not title and not location and not observed_condition and not recommended_action and not price_raw and not scope_items and not any(
            getattr(upload, "filename", "") for upload in uploads
        ):
            continue

        if status not in VALID_STATUSES:
            return error_page(f"issue {i + 1} must have a valid status.")

        if include_in_quote and not title:
            return error_page(f"issue {i + 1} is included in the quote but has no title.")

        price_value = Decimal("0.00")
        if price_raw:
            try:
                price_value = parse_money(price_raw)
            except InvalidOperation:
                return error_page(f"issue {i + 1} has an invalid price.")
        elif include_in_quote:
            return error_page(f"issue {i + 1} is included in the quote but has no price.")

        saved_photos = []
        valid_uploads = [upload for upload in uploads if getattr(upload, "filename", "")]
        if len(valid_uploads) > 4:
            return error_page(f"issue {i + 1} has more than 4 photos. maximum is 4.")

        for upload in valid_uploads[:4]:
            original_name = Path(upload.filename).name
            safe_name = f"{uuid.uuid4().hex}_{original_name}"
            photo_path = report_dir / safe_name

            with open(photo_path, "wb") as buffer:
                shutil.copyfileobj(upload.file, buffer)

            saved_photos.append(photo_path.resolve().as_uri())

        if status == "urgent":
            urgent_count += 1
        elif status == "recommended":
            recommended_count += 1
        elif status == "optional":
            optional_count += 1

        if include_in_quote:
            quote_row = {
                "item": title,
                "description": recommended_action or title,
                "location": location or "Not specified",
                "price": format_currency(price_value),
                "price_value": price_value,
            }

            if is_optional_quote_item:
                optional_quote_items.append(quote_row)
                optional_subtotal += price_value
            else:
                included_quote_items.append(quote_row)
                subtotal += price_value

        if include_in_quote and is_optional_quote_item:
            quote_status_text = "Optional item"
        elif include_in_quote:
            quote_status_text = "Included in final quote"
        else:
            quote_status_text = "Excluded"

        status_style = {
            "urgent": "status-urgent",
            "recommended": "status-recommended",
            "optional": "status-optional",
        }[status]

        photo_layout = {
            1: "photos-1",
            2: "photos-2",
            3: "photos-4",
            4: "photos-4",
        }.get(len(saved_photos), "photos-0")

        issues.append(
            {
                "number": len(issues) + 1,
                "title": title,
                "location": location or "Not specified",
                "status": status.title(),
                "status_style": status_style,
                "observed_condition": observed_condition or "No observed condition provided.",
                "recommended_action": recommended_action or "No recommendation provided.",
                "scope_items": scope_items,
                "price": format_currency(price_value),
                "price_value": price_value,
                "pricing_note": pricing_note,
                "include_in_quote": include_in_quote,
                "optional": is_optional_quote_item,
                "quote_status_text": quote_status_text,
                "photos": saved_photos,
                "photo_layout": photo_layout,
            }
        )

    if not issues:
        return error_page("add at least one issue before generating the report.")

    if not included_quote_items and not optional_quote_items:
        return error_page("at least one issue must be included in the quote.")

    logo_path = STATIC_DIR / "class-a-fix-logo.jpg"
    logo_url = logo_path.resolve().as_uri() if logo_path.exists() else None

    generated_date = datetime.now().strftime("%d %b %Y")
    download_name = f"inspection_report_{slugify(property_address)}.pdf"

    context = {
        "property_address": property_address,
        "agency": agency,
        "requested_by": requested_by,
        "work_order": work_order,
        "inspection_date": inspection_date,
        "priority": priority,
        "client_name": client_name,
        "tenant_name": tenant_name,
        "job_number": job_number,
        "estimated_timeframe": estimated_timeframe,
        "company_name": company_name,
        "company_phone": company_phone,
        "company_email": company_email,
        "company_website": company_website,
        "company_abn": company_abn,
        "generated_date": generated_date,
        "report_reference": report_reference,
        "logo_url": logo_url,
        "issues": issues,
        "total_issues": len(issues),
        "urgent_count": urgent_count,
        "recommended_count": recommended_count,
        "optional_count": optional_count,
        "subtotal": format_currency(subtotal),
        "optional_subtotal": format_currency(optional_subtotal),
        "total_estimated_cost": format_currency(subtotal),
        "included_quote_items": included_quote_items,
        "optional_quote_items": optional_quote_items,
        "has_optional_items": len(optional_quote_items) > 0,
    }

    html = templates.get_template("report.html").render(context)

    html_path = report_dir / "report.html"
    html_path.write_text(html, encoding="utf-8")

    pdf_path = report_dir / download_name

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--single-process",
            ],
        )

        page = await browser.new_page()
        await page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        await page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            margin={
                "top": "14mm",
                "right": "12mm",
                "bottom": "14mm",
                "left": "12mm",
            },
        )
        await browser.close()

    return FileResponse(
        pdf_path,
        media_type="application/pdf",
        filename=download_name,
    )
