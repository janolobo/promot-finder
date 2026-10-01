import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import search_web, SearchBlocked
from ddgs.exceptions import DDGSException, TimeoutException, RatelimitException

class SearchErrors(unittest.TestCase):
    def test_no_results_is_empty(self):
        with patch('ddgs.DDGS') as constructor, patch.object(constructor.return_value, 'text', side_effect=DDGSException('No results found.')):
            self.assertEqual(search_web('test','CZ',{}), [])
    def test_timeout_is_not_empty(self):
        with patch('ddgs.DDGS') as constructor, patch.object(constructor.return_value, 'text', side_effect=TimeoutException('timed out')):
            with self.assertRaisesRegex(ValueError, 'czas'):
                search_web('test','PL',{})
    def test_explicit_blocks(self):
        for exc in [RatelimitException('limit'),DDGSException('HTTP 429'),DDGSException('captcha')]:
            with patch('ddgs.DDGS') as constructor, patch.object(constructor.return_value, 'text', side_effect=exc):
                with self.assertRaises(SearchBlocked):
                    search_web('test','PL',{})
    def test_unknown_error_is_sanitized(self):
        with patch('ddgs.DDGS') as constructor, patch.object(constructor.return_value, 'text', side_effect=DDGSException('secret=xyz')):
            with self.assertRaisesRegex(ValueError,'poprawnej odpowiedzi') as error:
                search_web('test','PL',{})
            self.assertNotIn('xyz', str(error.exception))
