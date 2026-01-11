#!/usr/bin/env python3
"""
Script to create Stripe products and prices for the POS subscription plans.
Run this once to set up your Stripe catalog.

Products:
- Pro Plan: R250/month with 7-day free trial
- Business Plan: R350/month with 7-day free trial

Usage:
    cd point_of_sale/backend
    python scripts/setup_stripe_products.py
"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import stripe
import httpx

def get_stripe_credentials():
    """Fetch Stripe credentials from Replit connection API."""
    hostname = os.environ.get("REPLIT_CONNECTORS_HOSTNAME")
    x_replit_token = None
    
    if os.environ.get("REPL_IDENTITY"):
        x_replit_token = "repl " + os.environ["REPL_IDENTITY"]
    elif os.environ.get("WEB_REPL_RENEWAL"):
        x_replit_token = "depl " + os.environ["WEB_REPL_RENEWAL"]
    
    if not x_replit_token or not hostname:
        raise Exception("Cannot get Replit token for Stripe credentials")
    
    url = f"https://{hostname}/api/v2/connection"
    params = {
        "include_secrets": "true",
        "connector_names": "stripe",
        "environment": "development"
    }
    headers = {
        "Accept": "application/json",
        "X_REPLIT_TOKEN": x_replit_token
    }
    
    response = httpx.get(url, params=params, headers=headers)
    data = response.json()
    
    connection = data.get("items", [{}])[0]
    settings = connection.get("settings", {})
    
    if not settings.get("secret"):
        raise Exception("Stripe secret key not found in connection")
    
    return settings["secret"]

def setup_stripe_products():
    """Create Pro and Business subscription products with prices."""
    secret_key = get_stripe_credentials()
    stripe.api_key = secret_key
    
    print("Setting up Stripe products...")
    
    existing_products = stripe.Product.list(limit=100)
    existing_names = {p.name: p for p in existing_products.data}
    
    pro_product = existing_names.get("Pro Plan")
    if not pro_product:
        pro_product = stripe.Product.create(
            name="Pro Plan",
            description="Unlimited products, 3 users, CSV export, low-stock alerts, advanced reports",
            metadata={
                "plan_type": "pro",
                "max_users": "3",
                "features": "csv_export,low_stock_alerts,advanced_reports"
            }
        )
        print(f"Created Pro Plan product: {pro_product.id}")
    else:
        print(f"Pro Plan already exists: {pro_product.id}")
    
    business_product = existing_names.get("Business Plan")
    if not business_product:
        business_product = stripe.Product.create(
            name="Business Plan",
            description="Everything in Pro plus unlimited users and audit logs",
            metadata={
                "plan_type": "business",
                "max_users": "unlimited",
                "features": "csv_export,low_stock_alerts,advanced_reports,audit_logs"
            }
        )
        print(f"Created Business Plan product: {business_product.id}")
    else:
        print(f"Business Plan already exists: {business_product.id}")
    
    pro_prices = stripe.Price.list(product=pro_product.id, active=True)
    pro_price = None
    for p in pro_prices.data:
        if p.recurring and p.recurring.interval == "month" and p.unit_amount == 25000:
            pro_price = p
            break
    
    if not pro_price:
        pro_price = stripe.Price.create(
            product=pro_product.id,
            unit_amount=25000,
            currency="zar",
            recurring={"interval": "month", "trial_period_days": 7},
            metadata={"plan_type": "pro"}
        )
        print(f"Created Pro price: {pro_price.id} (R250/month with 7-day trial)")
    else:
        print(f"Pro price already exists: {pro_price.id}")
    
    business_prices = stripe.Price.list(product=business_product.id, active=True)
    business_price = None
    for p in business_prices.data:
        if p.recurring and p.recurring.interval == "month" and p.unit_amount == 35000:
            business_price = p
            break
    
    if not business_price:
        business_price = stripe.Price.create(
            product=business_product.id,
            unit_amount=35000,
            currency="zar",
            recurring={"interval": "month", "trial_period_days": 7},
            metadata={"plan_type": "business"}
        )
        print(f"Created Business price: {business_price.id} (R350/month with 7-day trial)")
    else:
        print(f"Business price already exists: {business_price.id}")
    
    print("\n" + "="*60)
    print("STRIPE PRODUCTS SETUP COMPLETE")
    print("="*60)
    print(f"\nPro Plan:")
    print(f"  Product ID: {pro_product.id}")
    print(f"  Price ID:   {pro_price.id}")
    print(f"  Amount:     R250/month (7-day trial)")
    print(f"\nBusiness Plan:")
    print(f"  Product ID: {business_product.id}")
    print(f"  Price ID:   {business_price.id}")
    print(f"  Amount:     R350/month (7-day trial)")
    print("\nAdd these Price IDs to your environment variables:")
    print(f"  STRIPE_PRO_PRICE_ID={pro_price.id}")
    print(f"  STRIPE_BUSINESS_PRICE_ID={business_price.id}")

if __name__ == "__main__":
    setup_stripe_products()
