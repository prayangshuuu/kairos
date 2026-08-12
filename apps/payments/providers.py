import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import stripe
from django.conf import settings
from django.utils import timezone

from paystation import PayStation

logger = logging.getLogger(__name__)

# Set the Stripe API key at the module level
stripe.api_key = getattr(settings, 'STRIPE_SECRET_KEY', '')


@dataclass
class CheckoutResult:
    """Returned by create_checkout — contains the redirect URL and session ID."""
    redirect_url: str
    session_id: str
    expires_at: Optional[datetime] = None


@dataclass
class RefundResult:
    """Returned by refund — contains the refund ID and status."""
    refund_id: str
    status: str
    amount_refunded_cents: int
    fee_refunded_cents: int = 0


class PaymentProvider(ABC):
    name: str  # e.g. 'stripe_connect', 'paystation'
    is_marketplace: bool = False  # True for Connect, False for direct merchant
    
    @abstractmethod
    def create_checkout(self, payment, success_url: str, cancel_url: str) -> CheckoutResult:
        """Create a checkout session and return the redirect URL."""
        ...
    
    @abstractmethod
    def refund(self, payment, amount_cents: Optional[int] = None) -> RefundResult:
        """Refund a payment. If amount_cents is None, full refund."""
        ...
    
    @abstractmethod
    def get_session_status(self, session_id: str, connected_account_id: Optional[str] = None) -> dict:
        """Get the current status of a checkout session from the provider."""
        ...
    
    @abstractmethod
    def verify_webhook_signature(self, payload: bytes, signature: str, secret: str) -> dict:
        """Verify and parse a webhook payload. Return the parsed event dict. Raise ValueError on invalid signature."""
        ...


class StripeConnectProvider(PaymentProvider):
    name = 'stripe_connect'
    is_marketplace = True
    
    def create_checkout(self, payment, success_url: str, cancel_url: str) -> CheckoutResult:
        try:
            connected_account_id = payment.payment_account.external_account_id
            
            # Default to 30 as Stripe requires expires_at to be at least 30 minutes in the future
            hold_ttl_minutes = getattr(settings, 'KAIROS_SLOT_HOLD_TTL_MINUTES', 30)
            
            # Calculate expiration time in seconds (Unix timestamp)
            expires_at_ts = int(timezone.now().timestamp()) + (hold_ttl_minutes * 60)
            
            session_params = {
                'payment_method_types': ['card'],
                'line_items': [{
                    'price_data': {
                        'currency': payment.currency,
                        'unit_amount': payment.amount_cents,
                        'product_data': {
                            'name': f'Payment for {payment.invoice_number}',
                        },
                    },
                    'quantity': 1,
                }],
                'mode': 'payment',
                'success_url': f"{success_url}?session_id={{CHECKOUT_SESSION_ID}}",
                'cancel_url': cancel_url,
                'client_reference_id': str(payment.uid),
                'metadata': {
                    'payment_uid': str(payment.uid),
                    'invoice_number': payment.invoice_number
                },
                'payment_intent_data': {
                    'application_fee_amount': payment.fee_amount_cents,
                },
                'expires_at': expires_at_ts,
                'stripe_account': connected_account_id,
            }
            
            # Add idempotency key
            idempotency_key = f'checkout_{payment.invoice_number}'
            
            session = stripe.checkout.Session.create(
                **session_params,
                idempotency_key=idempotency_key
            )
            
            expires_at_dt = datetime.fromtimestamp(session.expires_at, tz=timezone.utc) if session.get('expires_at') else None
            
            return CheckoutResult(
                redirect_url=session.url,
                session_id=session.id,
                expires_at=expires_at_dt
            )
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating checkout: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error creating checkout: {str(e)}", exc_info=True)
            raise

    def refund(self, payment, amount_cents: Optional[int] = None) -> RefundResult:
        try:
            connected_account_id = payment.payment_account.external_account_id
            payment_intent_id = payment.external_payment_intent_id
            
            if amount_cents is None:
                amount_cents = payment.amount_cents
                
            # Platform fee is refunded proportionally because keeping a fee on a cancelled meeting is indefensible.
            refund_obj = stripe.Refund.create(
                payment_intent=payment_intent_id,
                amount=amount_cents,
                refund_application_fee=True,  # Refund platform fee proportionally
                stripe_account=connected_account_id
            )
            
            return RefundResult(
                refund_id=refund_obj.id,
                status=refund_obj.status,
                amount_refunded_cents=refund_obj.amount,
                fee_refunded_cents=0  # Could be extracted from fee details if needed
            )
            
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error refunding payment: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error refunding payment: {str(e)}", exc_info=True)
            raise

    def get_session_status(self, session_id: str, connected_account_id: Optional[str] = None) -> dict:
        try:
            session = stripe.checkout.Session.retrieve(
                session_id,
                stripe_account=connected_account_id
            )
            return {
                'status': session.status,
                'payment_intent': session.payment_intent,
                'payment_status': session.payment_status
            }
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error retrieving session status: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error retrieving session status: {str(e)}", exc_info=True)
            raise

    def verify_webhook_signature(self, payload: bytes, signature: str, secret: str) -> dict:
        try:
            event = stripe.Webhook.construct_event(
                payload, signature, secret
            )
            return event
        except stripe.error.SignatureVerificationError as e:
            logger.error(f"Invalid Stripe webhook signature: {str(e)}")
            raise ValueError(f"Invalid signature: {str(e)}")
        except Exception as e:
            logger.error(f"Error verifying webhook signature: {str(e)}", exc_info=True)
            raise

    def create_connected_account(self, user, country: str = 'US') -> str:
        try:
            account = stripe.Account.create(
                type='express',
                country=country,
                email=user.email,
                capabilities={
                    'card_payments': {'requested': True},
                    'transfers': {'requested': True},
                },
            )
            return account.id
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating connected account: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error creating connected account: {str(e)}", exc_info=True)
            raise

    def create_account_link(self, account_id: str, return_url: str, refresh_url: str) -> str:
        try:
            account_link = stripe.AccountLink.create(
                account=account_id,
                refresh_url=refresh_url,
                return_url=return_url,
                type='account_onboarding',
            )
            return account_link.url
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating account link: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error creating account link: {str(e)}", exc_info=True)
            raise

    def retrieve_account(self, account_id: str) -> dict:
        try:
            account = stripe.Account.retrieve(account_id)
            return account
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error retrieving account: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error retrieving account: {str(e)}", exc_info=True)
            raise

    def create_express_login_link(self, account_id: str) -> str:
        try:
            login_link = stripe.Account.create_login_link(account_id)
            return login_link.url
        except stripe.error.StripeError as e:
            logger.error(f"Stripe error creating express login link: {str(e)}", exc_info=True)
            raise
        except Exception as e:
            logger.error(f"Error creating express login link: {str(e)}", exc_info=True)
            raise

# TODO: Enabling the PayStation host route in production requires confirming the 
# money-transmission and licensing position with a qualified professional first.
# This creates a real regulatory obligation because Kairos collects on behalf of the host.

class PayStationProvider(PaymentProvider):
    name = 'paystation'
    is_marketplace = False
    
    def __init__(self):
        self.merchant_id = getattr(settings, 'PAYSTATION_MERCHANT_ID', '104-1653730183')
        self.password = getattr(settings, 'PAYSTATION_PASSWORD', 'gamecoderstorepass')
        self.sandbox = getattr(settings, 'PAYSTATION_SANDBOX', True)
        self.client = PayStation(
            merchant_id=self.merchant_id,
            password=self.password,
            sandbox=self.sandbox,
        )
    
    def create_checkout(self, payment, success_url: str, cancel_url: str) -> CheckoutResult:
        guest_name = "Guest User"
        guest_email = "guest@example.com"
        guest_phone = "01700000000"
        
        # Determine actual guest info if booking relationship exists
        if hasattr(payment, 'booking') and payment.booking:
            guest_name = payment.booking.invitee_name or guest_name
            guest_email = payment.booking.invitee_email or guest_email
            
        amount = payment.amount_cents
        
        # Paystation typically uses redirect or POST. The SDK returns payment_url for redirect.
        # Ensure success URL captures session properly
        session_id = f"ps_{payment.uid}"
        callback_url = f"{success_url}?session_id={session_id}"
        
        try:
            response = self.client.initiate_payment(
                invoice_number=payment.invoice_number,
                payment_amount=amount,
                cust_name=guest_name,
                cust_phone=guest_phone,
                cust_email=guest_email,
                callback_url=callback_url,
            )
            
            if response.get("status") == "success":
                redirect_url = response.get("payment_url")
            else:
                logger.error(f"PayStation error response: {response}")
                redirect_url = cancel_url
                
        except Exception as e:
            logger.error(f"Error calling PayStation initiate_payment: {e}")
            redirect_url = cancel_url
        
        # Calculate expiration time
        hold_ttl_minutes = getattr(settings, 'KAIROS_SLOT_HOLD_TTL_MINUTES', 30)
        expires_at = timezone.now() + timezone.timedelta(minutes=hold_ttl_minutes)
        
        return CheckoutResult(
            redirect_url=redirect_url,
            session_id=session_id,
            expires_at=expires_at
        )

    def refund(self, payment, amount_cents: Optional[int] = None) -> RefundResult:
        if amount_cents is None:
            amount_cents = payment.amount_cents
            
        # PayStation refund logic goes here
        # Assuming success for now
        return RefundResult(
            refund_id=f"ref_{payment.uid}",
            status="succeeded",
            amount_refunded_cents=amount_cents,
            fee_refunded_cents=0
        )

    def get_session_status(self, session_id: str, connected_account_id: Optional[str] = None) -> dict:
        # For PayStation, the session is usually tied to the invoice. 
        # In this simplistic setup, we might need to parse invoice from session_id or store it.
        # The SDK check uses get_transaction_status_by_invoice. For now, mock success if needed or implement real check.
        # Assuming our callback validates or we just return complete to allow webhook to do the real work.
        return {
            'status': 'complete',
            'payment_intent': f"pi_{session_id}",
            'payment_status': 'paid'
        }

    def verify_webhook_signature(self, payload: bytes, signature: str, secret: str) -> dict:
        # Mock payload verification
        import json
        return json.loads(payload.decode('utf-8'))
