"""Production-grade crawler orchestrator."""
import asyncio
import time
from typing import List, Dict, Any, Optional
from urllib.parse import urlparse, urljoin
from collections import deque
import os
from pathlib import Path


class CrawlState:
    """Persist crawl state for checkpointing."""
    
    def __init__(self, state_file: str = "./data/crawl_state.json"):
        self.state_file = state_file
        Path(state_file).parent.mkdir(parents=True, exist_ok=True)
        self.state = {
            'visited': set(),
            'frontier': deque(),
            'extracted_count': 0,
            'start_time': None,
            'last_checkpoint': None
        }
    
    def save(self):
        """Save state to disk."""
        import json
        state_copy = {
            'visited': list(self.state['visited']),
            'frontier': list(self.state['frontier']),
            'extracted_count': self.state['extracted_count'],
            'start_time': self.state['start_time'],
            'last_checkpoint': time.time()
        }
        with open(self.state_file, 'w') as f:
            json.dump(state_copy, f)
    
    def load(self):
        """Load state from disk."""
        import json
        try:
            with open(self.state_file, 'r') as f:
                state_copy = json.load(f)
            self.state['visited'] = set(state_copy['visited'])
            self.state['frontier'] = deque(state_copy['frontier'])
            self.state['extracted_count'] = state_copy['extracted_count']
            self.state['start_time'] = state_copy['start_time']
            self.state['last_checkpoint'] = state_copy['last_checkpoint']
            return True
        except FileNotFoundError:
            return False


class ProductionCrawler:
    """Production-grade crawler orchestrator."""
    
    def __init__(self, config):
        self.config = config
        self.state = CrawlState()
        self.results = []
        self.semaphore = asyncio.Semaphore(config.concurrency.max_workers)
        
# Import components
        from my_scraper.utils.politeness import PolitenessManager
        from my_scraper.utils.retry import RetryManager
        from my_scraper.fetcher.smart_fetcher import SmartFetcher, RawHtmlStorage
        from my_scraper.parser.universal_parser import UniversalParser
        from my_scraper.storage.multi_format import StorageManager
        from my_scraper.compliance.manager import ComplianceManager
        try:
            from my_scraper.logging import StructuredLogger, log_request, log_parsing
            import sys
            print(f"SUCCESS: Imported log_request={log_request}, log_parsing={log_parsing}", file=sys.stderr)
        except ImportError as e:
            import sys
            print(f"LOGGING IMPORT ERROR: {e}", file=sys.stderr)
            StructuredLogger = None
            log_request = None
            log_parsing = None
        
        self.politeness = PolitenessManager(config)
        self.retry_manager = RetryManager(config)
        self.fetcher = SmartFetcher(config)
        self.parser = UniversalParser(config.required_fields)
        self.storage = StorageManager(config)
        self.compliance = ComplianceManager(config)
        self.logger = StructuredLogger(
            config.name,
            config.logging.log_file,
            config.logging.audit_log,
            config.logging.json_format
        )
        self.raw_storage = RawHtmlStorage(config.storage.blob_storage_path)
        
        # Store log functions as instance attributes
        self.log_request = log_request
        self.log_parsing = log_parsing
    
    async def crawl(self, start_urls: List[str] = None, selectors: Dict[str, str] = None):
        """Start crawling."""
        if start_urls is None:
            start_urls = self.config.start_urls
        
        self.state.state['start_time'] = time.time()
        self.logger.info("crawl_started", start_urls=start_urls, max_depth=self.config.max_depth)
        
        # Initialize frontier
        for url in start_urls:
            if url not in self.state.state['visited']:
                self.state.state['frontier'].append((url, 0))  # (url, depth)
        
        # Crawl
        tasks = []
        while self.state.state['frontier'] and len(self.results) < self.config.max_pages:
            url, depth = self.state.state['frontier'].popleft()
            
            if url in self.state.state['visited']:
                continue
            
            if depth > self.config.max_depth:
                continue
            
            if urlparse(url).netloc not in self.config.allowed_domains and self.config.allowed_domains:
                continue
            
            # Create task
            task = asyncio.create_task(self.process_url(url, depth, selectors))
            tasks.append(task)
            
            # Limit concurrent tasks
            if len(tasks) >= self.config.concurrency.max_workers:
                await asyncio.gather(*tasks, return_exceptions=True)
                tasks = []
            
            # Checkpoint periodically
            if len(self.results) % 100 == 0:
                self.state.save()
                self.logger.info("checkpoint_saved", extracted_count=len(self.results))
        
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        
        # Final checkpoint
        self.state.save()
        
        # Export results
        export_results = self.storage.export(self.results)
        
        self.logger.info("crawl_completed", 
                        total_items=len(self.results),
                        duration_seconds=round(time.time() - self.state.state['start_time'], 2),
                        exports=export_results)
        
        # Generate compliance report
        compliance_report = self.compliance.generate_compliance_report()
        self.logger.info("compliance_report", **compliance_report)
        
        return self.results
    
    async def process_url(self, url: str, depth: int, selectors: Dict[str, str] = None):
        """Process a single URL."""
        async with self.semaphore:
            try:
                # Compliance check
                self.compliance.validate_url(url)
                
                # Politeness checks
                if not await self.politeness.can_fetch(url):
                    self.logger.warning("robots_disallowed", url=url)
                    self.compliance.log_scrape_event(url, "robots_disallowed")
                    return
                
                await self.politeness.wait_for_slot(url)
                
                # Fetch
                start_time = time.time()
                fetch_result = await self.retry_manager.retry_http(
                    self.fetcher.fetch, url
                )
                duration_ms = (time.time() - start_time) * 1000
                
                self.log_request(self.logger, url, 'GET', fetch_result['status_code'], 
                           duration_ms, fetch_resulter_type=fetch_result.get('fetcher_type'))
                
                # Save raw HTML
                if self.config.storage.save_raw_html:
                    html_pointer = self.raw_storage.save(url, fetch_result['content'])
                
                # Parse
                parsed = self.parser.parse(fetch_result, selectors)
                
                if parsed.data and parsed.data != {'html': fetch_result['content']}:
                    # Check for personal data
                    personal_data = self.compliance.scan_for_personal_data(parsed.data)
                    
                    # Data minimization
                    if self.config.required_fields:
                        parsed.data = self.compliance.check_data_minimization(parsed.data, self.config.required_fields)
                    
                    self.results.append(parsed.data)
                    self.state.state['extracted_count'] += 1
                    
                    self.log_parsing(self.logger, url, 1, len(parsed.errors))
                    self.compliance.log_scrape_event(url, "success", 1, personal_data)
                else:
                    self.log_parsing(self.logger, url, 0, len(parsed.errors))
                    self.compliance.log_scrape_event(url, "no_data_extracted")
                
                self.state.state['visited'].add(url)
                
                # Follow links if depth allows
                if depth < self.config.max_depth and selectors:
                    self.extract_links(fetch_result['content'], url, depth)
                
            except Exception as e:
                self.logger.error("url_processing_failed", url=url, error=str(e))
                self.compliance.log_scrape_event(url, "failed", error=str(e))
                self.politeness.handle_error(url, 500)
    
    def extract_links(self, content: str, base_url: str, depth: int):
        """Extract and enqueue links."""
        from parsel import Selector
        sel = Selector(text=content)
        links = sel.css('a::attr(href)').getall()
        
        for link in links:
            try:
                full_url = urljoin(base_url, link)
                parsed = urlparse(full_url)
                
                if parsed.scheme not in ['http', 'https']:
                    continue
                
                if self.config.allowed_domains and parsed.netloc not in self.config.allowed_domains:
                    continue
                
                if full_url not in self.state.state['visited']:
                    self.state.state['frontier'].append((full_url, depth + 1))
            except:
                continue
