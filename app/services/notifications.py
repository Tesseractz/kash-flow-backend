"""
Email notification infrastructure using Brevo (formerly Sendinblue).
Free tier: 300 emails/day (~9,000/month)
"""

import os
import httpx
from typing import Optional, List
from pydantic import BaseModel
from enum import Enum


class NotificationType(str, Enum):
    LOW_STOCK = "low_stock"
    RECEIPT = "receipt"


class NotificationConfig(BaseModel):
    email_enabled: bool = False
    low_stock_threshold: int = 10
    notification_email: Optional[str] = None


class ReceiptRequest(BaseModel):
    sale_id: int
    customer_email: Optional[str] = None
    send_email: bool = False
    # Payment info for receipt
    payment_method: Optional[str] = "cash"  # "cash" or "card"
    payment_amount: Optional[float] = None  # Amount tendered
    change_amount: Optional[float] = None   # Change given


class NotificationResult(BaseModel):
    success: bool
    message: str
    recipient: Optional[str] = None


def is_email_configured() -> bool:
    """Check if Brevo email service is configured."""
    return bool(os.getenv("BREVO_API_KEY"))


def send_email(
    to_email: str,
    subject: str,
    html_body: str
) -> NotificationResult:
    """
    Send email via Brevo API.
    Free tier: 300 emails/day.
    
    Setup:
    1. Create account at brevo.com
    2. Go to Settings > Senders & IP > Add a Sender (verify your email)
    3. Get API key from Settings > SMTP & API > API Keys
    4. Set environment variables:
       - BREVO_API_KEY=your_api_key
       - BREVO_SENDER_EMAIL=your_verified_email@example.com
    """
    api_key = os.getenv("BREVO_API_KEY")
    
    if not api_key:
        return NotificationResult(
            success=False,
            message="Email not configured. Set BREVO_API_KEY in your environment.",
            recipient=to_email
        )
    
    # IMPORTANT: This email MUST be verified in Brevo dashboard
    # Go to: Settings > Senders & IP > Add a Sender
    sender_email = os.getenv("BREVO_SENDER_EMAIL")
    sender_name = os.getenv("BREVO_SENDER_NAME", "KashPoint")
    
    if not sender_email:
        return NotificationResult(
            success=False,
            message="BREVO_SENDER_EMAIL not set. Add a verified sender email from your Brevo account.",
            recipient=to_email
        )
    
    try:
        response = httpx.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "sender": {
                    "name": sender_name,
                    "email": sender_email
                },
                "to": [{"email": to_email}],
                "subject": subject,
                "htmlContent": html_body
            },
            timeout=15.0
        )
        
        # Log for debugging
        print(f"[Brevo] Status: {response.status_code}, To: {to_email}")
        
        if response.status_code in (200, 201, 202):
            return NotificationResult(
                success=True,
                message="Email sent successfully",
                recipient=to_email
            )
        else:
            error_data = {}
            try:
                error_data = response.json() if response.content else {}
            except:
                pass
            
            error_msg = error_data.get("message", f"HTTP {response.status_code}")
            error_code = error_data.get("code", "")
            
            # Provide helpful messages for common errors
            if "sender" in error_msg.lower() or error_code == "unauthorized_sender":
                error_msg = f"Sender email not verified in Brevo. Verify '{sender_email}' at Settings > Senders & IP"
            elif "unauthorized" in error_msg.lower():
                error_msg = "Invalid API key. Check your BREVO_API_KEY"
            
            print(f"[Brevo] Error: {error_msg} | Full response: {error_data}")
            
            return NotificationResult(
                success=False,
                message=error_msg,
                recipient=to_email
            )
    except httpx.TimeoutException:
        return NotificationResult(
            success=False,
            message="Email sending timed out. Please try again.",
            recipient=to_email
        )
    except Exception as e:
        return NotificationResult(
            success=False,
            message=f"Failed to send email: {str(e)}",
            recipient=to_email
        )


def generate_receipt_html(sale_data: dict, store_name: str = "KashPoint") -> str:
    """Generate HTML receipt for email."""
    items_html = ""
    for item in sale_data.get("items", []):
        items_html += f"""
        <tr>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0;">{item.get('name', 'Product')}</td>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0; text-align: center;">{item.get('quantity', 1)}</td>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0; text-align: right;">R {item.get('price', 0):.2f}</td>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0; text-align: right; font-weight: 600;">R {item.get('total', 0):.2f}</td>
        </tr>
        """
    
    # Get payment info if available
    payment_method = sale_data.get('payment_method', 'cash')
    tendered = sale_data.get('payment_amount', sale_data.get('total', 0))
    change = sale_data.get('change', 0)
    
    payment_section = ""
    if payment_method == 'cash' and change > 0:
        payment_section = f"""
        <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #e2e8f0;">
            <table style="width: 100%;">
                <tr>
                    <td style="color: #64748b;">Payment Method:</td>
                    <td style="text-align: right;">Cash</td>
                </tr>
                <tr>
                    <td style="color: #64748b;">Amount Tendered:</td>
                    <td style="text-align: right;">R {tendered:.2f}</td>
                </tr>
                <tr>
                    <td style="color: #059669; font-weight: 600;">Change:</td>
                    <td style="text-align: right; color: #059669; font-weight: 600; font-size: 16px;">R {change:.2f}</td>
                </tr>
            </table>
        </div>
        """
    elif payment_method == 'card':
        payment_section = f"""
        <div style="margin-top: 10px; padding-top: 10px; border-top: 1px solid #e2e8f0;">
            <table style="width: 100%;">
                <tr>
                    <td style="color: #64748b;">Payment Method:</td>
                    <td style="text-align: right;">💳 Card</td>
                </tr>
            </table>
        </div>
        """
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Receipt from {store_name}</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8fafc;">
        <div style="background-color: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
            <!-- Header -->
            <div style="background: linear-gradient(135deg, #3b82f6 0%, #2563eb 100%); padding: 30px; text-align: center;">
                <h1 style="color: white; margin: 0; font-size: 24px;">{store_name}</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 8px 0 0 0; font-size: 14px;">Receipt #{sale_data.get('id', 'N/A')}</p>
            </div>
            
            <!-- Date -->
            <div style="padding: 15px 20px; background-color: #f8fafc; border-bottom: 1px solid #e2e8f0; text-align: center;">
                <p style="color: #64748b; margin: 0; font-size: 13px;">{sale_data.get('timestamp', '')}</p>
        </div>
        
            <!-- Items -->
            <div style="padding: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="background-color: #f8fafc;">
                            <th style="padding: 12px 8px; text-align: left; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Item</th>
                            <th style="padding: 12px 8px; text-align: center; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Qty</th>
                            <th style="padding: 12px 8px; text-align: right; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Price</th>
                            <th style="padding: 12px 8px; text-align: right; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Total</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>
        
                <!-- Total -->
                <div style="margin-top: 20px; padding: 20px; background-color: #f8fafc; border-radius: 8px;">
                    <div style="display: flex; justify-content: space-between; align-items: center;">
                        <span style="font-size: 16px; color: #334155; font-weight: 600;">Total Amount</span>
                        <span style="font-size: 24px; font-weight: 700; color: #1e293b;">R {sale_data.get('total', 0):.2f}</span>
                    </div>
                    {payment_section}
                </div>
        </div>
        
            <!-- Footer -->
            <div style="padding: 20px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="color: #059669; font-weight: 600; margin: 0 0 5px 0;">Thank you for your purchase! 🎉</p>
                <p style="color: #94a3b8; font-size: 12px; margin: 0;">Powered by KashPoint</p>
            </div>
        </div>
    </body>
    </html>
    """


def generate_low_stock_email(products: List[dict], store_name: str = "KashPoint") -> tuple:
    """Generate low stock alert email. Returns (subject, html_body)."""
    items_html = ""
    for p in products:
        qty = p.get('quantity', 0)
        qty_color = "#dc2626" if qty <= 5 else "#f59e0b"
        items_html += f"""
        <tr>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0;">{p.get('name', 'Unknown')}</td>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0; color: #64748b;">{p.get('sku', 'N/A')}</td>
            <td style="padding: 12px 8px; border-bottom: 1px solid #e2e8f0; text-align: center;">
                <span style="background-color: {qty_color}; color: white; padding: 4px 12px; border-radius: 12px; font-weight: 600; font-size: 13px;">{qty}</span>
            </td>
        </tr>
        """
    
    subject = f"⚠️ [{store_name}] Low Stock Alert - {len(products)} products need attention"
    
    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Low Stock Alert</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8fafc;">
        <div style="background-color: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
            <!-- Alert Header -->
            <div style="background: linear-gradient(135deg, #ef4444 0%, #dc2626 100%); padding: 30px; text-align: center;">
                <div style="font-size: 40px; margin-bottom: 10px;">⚠️</div>
                <h1 style="color: white; margin: 0; font-size: 22px;">Low Stock Alert</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 8px 0 0 0;">{len(products)} product(s) need restocking</p>
        </div>
        
            <!-- Products Table -->
            <div style="padding: 20px;">
                <table style="width: 100%; border-collapse: collapse;">
            <thead>
                <tr style="background-color: #f8fafc;">
                            <th style="padding: 12px 8px; text-align: left; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Product</th>
                            <th style="padding: 12px 8px; text-align: left; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Product Code</th>
                            <th style="padding: 12px 8px; text-align: center; font-size: 12px; text-transform: uppercase; color: #64748b; border-bottom: 2px solid #e2e8f0;">Stock</th>
                </tr>
            </thead>
            <tbody>
                {items_html}
            </tbody>
        </table>
            </div>
        
            <!-- Action -->
            <div style="padding: 20px; background-color: #fef2f2; border-top: 1px solid #fecaca;">
                <p style="color: #991b1b; margin: 0; text-align: center;">
            Please restock these items soon to avoid running out.
        </p>
            </div>
            
            <!-- Footer -->
            <div style="padding: 15px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="color: #94a3b8; font-size: 12px; margin: 0;">Sent by KashPoint</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    return subject, html_body


def generate_daily_summary_email(summary: dict, store_name: str = "KashPoint") -> tuple:
    """Generate daily finance summary email. Returns (subject, html_body)."""
    date_label = summary.get("date_label", "Today")
    totals = summary.get("totals", {})
    sales_count = totals.get("total_sales_count", 0)
    revenue = float(totals.get("total_revenue", 0))
    profit = float(totals.get("total_profit", 0))

    subject = f"📊 [{store_name}] Daily Summary - {date_label}"

    html_body = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Daily Summary</title>
    </head>
    <body style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 20px; background-color: #f8fafc;">
        <div style="background-color: white; border-radius: 12px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden;">
            <div style="background: linear-gradient(135deg, #0ea5e9 0%, #2563eb 100%); padding: 30px; text-align: center;">
                <div style="font-size: 40px; margin-bottom: 10px;">📊</div>
                <h1 style="color: white; margin: 0; font-size: 22px;">Daily Finance Summary</h1>
                <p style="color: rgba(255,255,255,0.9); margin: 8px 0 0 0;">{date_label}</p>
            </div>
            <div style="padding: 20px;">
                <div style="display: flex; flex-direction: column; gap: 12px;">
                    <div style="display: flex; justify-content: space-between; background: #f8fafc; padding: 12px 16px; border-radius: 10px;">
                        <span style="color: #64748b;">Sales Count</span>
                        <strong style="color: #0f172a;">{sales_count}</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between; background: #f8fafc; padding: 12px 16px; border-radius: 10px;">
                        <span style="color: #64748b;">Total Revenue</span>
                        <strong style="color: #0f172a;">R {revenue:.2f}</strong>
                    </div>
                    <div style="display: flex; justify-content: space-between; background: #f8fafc; padding: 12px 16px; border-radius: 10px;">
                        <span style="color: #64748b;">Total Profit</span>
                        <strong style="color: #0f172a;">R {profit:.2f}</strong>
                    </div>
                </div>
            </div>
            <div style="padding: 15px; text-align: center; border-top: 1px solid #e2e8f0;">
                <p style="color: #94a3b8; font-size: 12px; margin: 0;">Sent by KashPoint</p>
            </div>
        </div>
    </body>
    </html>
    """

    return subject, html_body
