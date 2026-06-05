"""
email_parser.py — Modul parser email notifikasi bank via IMAP
Mendukung: Bank Jago, Jenius, Sinarmas
"""

import imaplib
import email
from email.header import decode_header
import re
from datetime import datetime, timedelta
from typing import Optional
import streamlit as st


# ══════════════════════════════════════════════════════════════════════════════
# IMAP CONNECTION
# ══════════════════════════════════════════════════════════════════════════════

def connect_gmail(email_addr: str, app_password: str) -> Optional[imaplib.IMAP4_SSL]:
    """
    Konek ke Gmail via IMAP menggunakan App Password.
    App Password dibuat di: https://myaccount.google.com/apppasswords
    """
    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_addr, app_password)
        return mail
    except imaplib.IMAP4.error as e:
        st.error(f"Gagal konek Gmail: {e}")
        return None


def fetch_emails(mail: imaplib.IMAP4_SSL, folder: str = "INBOX",
                 days_back: int = 7, sender_filter: str = "") -> list[dict]:
    """
    Ambil email dari folder tertentu, filter berdasarkan sender dan rentang hari.
    """
    mail.select(folder)

    since_date = (datetime.now() - timedelta(days=days_back)).strftime("%d-%b-%Y")
    search_criteria = f'(SINCE "{since_date}")'
    if sender_filter:
        search_criteria = f'(SINCE "{since_date}" FROM "{sender_filter}")'

    _, message_ids = mail.search(None, search_criteria)
    emails = []

    for msg_id in message_ids[0].split():
        _, msg_data = mail.fetch(msg_id, "(RFC822)")
        raw_email = msg_data[0][1]
        msg = email.message_from_bytes(raw_email)

        subject_raw, encoding = decode_header(msg["Subject"])[0]
        subject = subject_raw.decode(encoding or "utf-8") if isinstance(subject_raw, bytes) else subject_raw
        from_addr = msg.get("From", "")
        date_str  = msg.get("Date", "")

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    try:
                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                        break
                    except Exception:
                        pass
        else:
            try:
                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
            except Exception:
                pass

        emails.append({
            "subject": subject,
            "from":    from_addr,
            "date":    date_str,
            "body":    body,
        })

    return emails


# ══════════════════════════════════════════════════════════════════════════════
# BANK EMAIL PARSERS
# ══════════════════════════════════════════════════════════════════════════════

def parse_bank_jago(subject: str, body: str, email_date: str) -> Optional[dict]:
    """
    Parser notifikasi email Bank Jago.
    Subject biasanya: "Transaksi Debit Rp X,XXX dari rekening..."
    """
    try:
        tx_date = datetime.now().strftime("%Y-%m-%d")
        try:
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"]:
                try:
                    tx_date = datetime.strptime(email_date[:31].strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        # Deteksi tipe transaksi dari subject
        subject_lower = subject.lower()
        if any(k in subject_lower for k in ["debit", "keluar", "pembayaran", "transfer keluar"]):
            tx_type = "Expense"
        elif any(k in subject_lower for k in ["kredit", "masuk", "terima"]):
            tx_type = "Income"
        else:
            tx_type = "Expense"

        # Cari nominal di subject atau body
        amount_match = re.search(r"Rp\s*([\d\.,]+)", subject + " " + body)
        if not amount_match:
            return None
        amount_str = amount_match.group(1).replace(".", "").replace(",", "")
        amount = float(amount_str)

        # Cari deskripsi merchant/tujuan
        desc_patterns = [
            r"(?:ke|dari|untuk|merchant)[:\s]+([^\n\r,]{3,40})",
            r"(?:Transaksi di|Pembelian di)[:\s]+([^\n\r,]{3,40})",
        ]
        description = "Bank Jago Transaction"
        for pat in desc_patterns:
            m = re.search(pat, body, re.IGNORECASE)
            if m:
                description = m.group(1).strip()[:40]
                break

        return {
            "date":         tx_date,
            "description":  description,
            "amount":       amount,
            "type":         tx_type,
            "pocket":       "Bank Jago",
            "sub_category": "uncategorized",
            "category":     "Uncategorized",
            "source":       "email",
        }
    except Exception:
        return None


def parse_jenius(subject: str, body: str, email_date: str) -> Optional[dict]:
    """
    Parser notifikasi email Jenius / BTPN.
    """
    try:
        tx_date = datetime.now().strftime("%Y-%m-%d")
        try:
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"]:
                try:
                    tx_date = datetime.strptime(email_date[:31].strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        subject_lower = subject.lower()
        if any(k in subject_lower for k in ["debit", "payment", "transfer out", "belanja"]):
            tx_type = "Expense"
        elif any(k in subject_lower for k in ["credit", "incoming", "top up"]):
            tx_type = "Income"
        else:
            tx_type = "Expense"

        amount_match = re.search(r"IDR\s*([\d\.,]+)|Rp\s*([\d\.,]+)", subject + " " + body, re.IGNORECASE)
        if not amount_match:
            return None
        raw = (amount_match.group(1) or amount_match.group(2)).replace(".", "").replace(",", "")
        amount = float(raw)

        desc_match = re.search(r"(?:at|to|dari|merchant)[:\s]+([^\n\r]{3,40})", body, re.IGNORECASE)
        description = desc_match.group(1).strip()[:40] if desc_match else "Jenius Transaction"

        return {
            "date":         tx_date,
            "description":  description,
            "amount":       amount,
            "type":         tx_type,
            "pocket":       "Jenius",
            "sub_category": "uncategorized",
            "category":     "Uncategorized",
            "source":       "email",
        }
    except Exception:
        return None


def parse_sinarmas(subject: str, body: str, email_date: str) -> Optional[dict]:
    """
    Parser notifikasi email Bank Sinarmas.
    """
    try:
        tx_date = datetime.now().strftime("%Y-%m-%d")
        try:
            for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z"]:
                try:
                    tx_date = datetime.strptime(email_date[:31].strip(), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
        except Exception:
            pass

        subject_lower = subject.lower()
        tx_type = "Income" if any(k in subject_lower for k in ["credit", "masuk", "incoming"]) else "Expense"

        amount_match = re.search(r"Rp\.?\s*([\d\.,]+)", subject + " " + body, re.IGNORECASE)
        if not amount_match:
            return None
        raw = amount_match.group(1).replace(".", "").replace(",", "")
        amount = float(raw)

        desc_match = re.search(r"(?:transaksi|keterangan)[:\s]+([^\n\r]{3,40})", body, re.IGNORECASE)
        description = desc_match.group(1).strip()[:40] if desc_match else "Sinarmas Transaction"

        return {
            "date":         tx_date,
            "description":  description,
            "amount":       amount,
            "type":         tx_type,
            "pocket":       "Sinarmas",
            "sub_category": "uncategorized",
            "category":     "Uncategorized",
            "source":       "email",
        }
    except Exception:
        return None


# ══════════════════════════════════════════════════════════════════════════════
# ROUTER — deteksi bank dari sender email
# ══════════════════════════════════════════════════════════════════════════════

BANK_SENDER_MAP = {
    "jago":     ("Bank Jago",  parse_bank_jago),
    "btpn":     ("Jenius",     parse_jenius),
    "jenius":   ("Jenius",     parse_jenius),
    "sinarmas": ("Sinarmas",   parse_sinarmas),
}


def parse_email_to_transaction(em: dict) -> Optional[dict]:
    """Auto-detect bank dari sender dan parse ke format transaksi."""
    sender = em["from"].lower()
    for keyword, (bank_name, parser_fn) in BANK_SENDER_MAP.items():
        if keyword in sender:
            result = parser_fn(em["subject"], em["body"], em["date"])
            if result:
                result["bank_detected"] = bank_name
            return result
    return None


def fetch_and_parse_transactions(
    email_addr: str,
    app_password: str,
    days_back: int = 7,
) -> tuple[list[dict], list[dict], str]:
    """
    Main entry point: konek Gmail, fetch email, parse jadi transaksi.
    Return: (transactions_parsed, emails_raw, error_msg)
    """
    mail = connect_gmail(email_addr, app_password)
    if not mail:
        return [], [], "Gagal konek ke Gmail."

    all_transactions = []
    all_raw_emails   = []
    errors           = []

    for keyword in BANK_SENDER_MAP:
        try:
            emails = fetch_emails(mail, days_back=days_back, sender_filter=keyword)
            all_raw_emails.extend(emails)
            for em in emails:
                tx = parse_email_to_transaction(em)
                if tx:
                    all_transactions.append(tx)
        except Exception as e:
            errors.append(f"{keyword}: {e}")

    try:
        mail.logout()
    except Exception:
        pass

    error_msg = "; ".join(errors) if errors else ""
    return all_transactions, all_raw_emails, error_msg
