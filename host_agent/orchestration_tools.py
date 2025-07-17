"""Orchestration tools for the Host Agent."""
from typing import List


def get_import_keywords() -> List[str]:
    """Keywords related to imports - kept for instruction context."""
    return [
        "import", "imports", "importation", "importations",
        "customs", "customs office", "colombia", "colombian",
        "legalization", "process", "requirements", "documents",
        "dian", "certificate", "origin", "tariff",
        "foreign trade", "declaration", "duties"
    ]


def get_invoice_keywords() -> List[str]:
    """Keywords related to invoices - kept for instruction context."""
    return [
        "invoice", "invoices", "bill", "bills",
        "charge", "payment", "billing", "account",
        "amount", "total", "vat", "tax", "withholding",
        "client", "customer", "supplier", "provider",
        "date", "number", "receipt", "value"
    ]


def create_security_alert(detected_issues: list = None) -> str:
    """
    Creates a formatted security alert for the user.
    
    Args:
        detected_issues: List of issues detected by the verification agent
        
    Returns:
        Formatted alert message
    """
    alert = "🚨 **SECURITY ALERT** 🚨\n\n"
    alert += "I cannot process this request because it contains content outside my scope or potentially unsafe material.\n"
    
    if detected_issues:
        alert += f"\nIssues detected: {', '.join(detected_issues)}\n"
    
    alert += "\nPlease rephrase your query focusing on topics related to:\n"
    alert += "• Imports and customs processes in Colombia\n"
    alert += "• Information about invoices and commercial documents\n"
    
    return alert