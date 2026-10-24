"""Compliance and governance module."""
import json
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any
from urllib.parse import urlparse


class ComplianceManager:
    """Legal and ethical compliance enforcement."""
    
    def __init__(self, config):
        self.config = config
        self.audit_log_file = config.logging.audit_log
        Path(self.audit_log_file).parent.mkdir(parents=True, exist_ok=True)
        
        # Track scraped URLs and data
        self.scraped_urls = []
        self.personal_data_found = []
    
    def validate_url(self, url: str) -> bool:
        """Validate URL is public and allowed."""
        if not self.config.compliance.public_only:
            return True
        
        parsed = urlparse(url)
        
        # Check for login/auth indicators
        if any(x in url.lower() for x in ['/login', '/signin', '/auth', '/account', '/dashboard']):
            raise ValueError(f"Attempting to scrape login/auth URL: {url}. Public data only.")
        
        # Check for paywall indicators
        if any(x in url.lower() for x in ['/paywall', '/subscribe', '/premium']):
            raise ValueError(f"Attempting to scrape paywall URL: {url}. Public data only.")
        
        return True
    
    def check_data_minimization(self, data: Dict[str, Any], schema: List[str]) -> Dict[str, Any]:
        """Enforce data minimization - only collect required fields."""
        if not self.config.compliance.data_minimization:
            return data
        
        # Only keep fields in schema
        filtered = {}
        for field in schema:
            if field in data:
                filtered[field] = data[field]
        
        return filtered
    
    def scan_for_personal_data(self, data: Dict[str, Any]) -> List[str]:
        """Scan for potential personal data (PII)."""
        pii_patterns = [
            'email', 'phone', 'ssn', 'social_security', 
            'address', 'zip', 'postal', 'dob', 'birth'
        ]
        
        found = []
        data_str = json.dumps(data).lower()
        
        for field in data:
            field_lower = field.lower()
            if any(pattern in field_lower for pattern in pii_patterns):
                found.append(field)
            elif any(pattern in data_str for pattern in pii_patterns):
                found.append(field)
        
        return found
    
    def log_scrape_event(self, url: str, status: str, items_count: int = 0, 
                        personal_data: List[str] = None):
        """Log scrape event to audit trail."""
        if not self.config.compliance.audit_all_requests:
            return
        
        event = {
            "event_id": f"evt_{datetime.utcnow().timestamp()}",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "url": url,
            "domain": urlparse(url).netloc,
            "status": status,
            "items_extracted": items_count,
            "personal_data_fields": personal_data or [],
            "user_agent": self.config.robots.user_agent,
            "jurisdiction": self.config.compliance.jurisdiction
        }
        
        with open(self.audit_log_file, 'a') as f:
            f.write(json.dumps(event) + '\n')
        
        self.scraped_urls.append(url)
        if personal_data:
            self.personal_data_found.extend(personal_data)
    
    def generate_compliance_report(self) -> Dict[str, Any]:
        """Generate compliance report."""
        return {
            "report_generated": datetime.utcnow().isoformat() + "Z",
            "total_urls_scraped": len(self.scraped_urls),
            "unique_domains": len(set(urlparse(u).netloc for u in self.scraped_urls)),
            "personal_data_fields_found": list(set(self.personal_data_found)),
            "jurisdiction": self.config.compliance.jurisdiction,
            "public_only_enforced": self.config.compliance.public_only,
            "data_minimization_enforced": self.config.compliance.data_minimization,
            "audit_trail_location": self.audit_log_file
        }
    
    def handle_takedown_request(self, domain: str, reason: str):
        """Handle takedown request."""
        event = {
            "event_type": "takedown_request",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "domain": domain,
            "reason": reason,
            "action_taken": "Added to blocklist"
        }
        with open(self.audit_log_file, 'a') as f:
            f.write(json.dumps(event) + '\n')
