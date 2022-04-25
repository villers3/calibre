#!/usr/bin/env python
# License: GPLv3 Copyright: 2021, Kovid Goyal <kovid at kovidgoyal.net>


import builtins
import os
import shutil
import sys
import time
from io import BytesIO
from zipfile import ZipFile

from calibre.db.fts.text import html_to_text
from calibre.db.tests.base import BaseTest


def print(*args, **kwargs):
    kwargs['file'] = sys.__stdout__
    builtins.print(*args, **kwargs)


class FTSAPITest(BaseTest):
    ae = BaseTest.assertEqual

    def setUp(self):
        super().setUp()
        from calibre_extensions.sqlite_extension import set_ui_language
        set_ui_language('en')

    def tearDown(self):
        super().tearDown()
        from calibre_extensions.sqlite_extension import set_ui_language
        set_ui_language('en')

    def new_library(self):
        if os.path.exists(self.library_path):
            shutil.rmtree(self.library_path)
        os.makedirs(self.library_path)
        self.create_db(self.library_path)
        return self.init_cache()

    def wait_for_fts_to_finish(self, fts, timeout=10):
        if fts.pool.initialized:
            st = time.monotonic()
            while fts.all_currently_dirty() and time.monotonic() - st < timeout:
                fts.pool.supervisor_thread.join(0.01)

    def text_records(self, fts):
        return fts.get_connection().get_dict('SELECT * FROM fts_db.books_text')

    def make_txtz(self, txt, **extra):
        buf = BytesIO()
        with ZipFile(buf, mode='w') as zf:
            zf.writestr('index.txt', txt)
            for key, val in extra.items():
                zf.writestr(key, val)
        buf.seek(0)
        return buf

    def test_fts_pool(self):
        cache = self.new_library()
        fts = cache.enable_fts()
        self.wait_for_fts_to_finish(fts)
        self.assertFalse(fts.all_currently_dirty())
        cache.add_format(1, 'TXT', BytesIO(b'a test text'))
        self.wait_for_fts_to_finish(fts)

        def q(rec, **kw):
            self.ae({x: rec[x] for x in kw}, kw)

        def check(**kw):
            tr = self.text_records(fts)
            self.ae(len(tr), 1)
            q(tr[0], **kw)

        check(id=1, book=1, format='TXT', searchable_text='a test text')
        # check re-adding does not rescan
        cache.add_format(1, 'TXT', BytesIO(b'a test text'))
        self.wait_for_fts_to_finish(fts)
        check(id=1, book=1, format='TXT', searchable_text='a test text')
        # check updating rescans
        cache.add_format(1, 'TXT', BytesIO(b'a test text2'))
        self.wait_for_fts_to_finish(fts)
        check(id=2, book=1, format='TXT', searchable_text='a test text2')
        # check closing shuts down all workers
        cache.close()
        self.assertFalse(fts.pool.initialized)

        # TODO: check shutdown when worker hangs
        # TODO: add a max scan time and check that the worker honors it
        # TODO: Add a column to store failures with tracebacks in the books_text table

        # check enabling scans pre-exisintg
        cache = self.new_library()
        cache.add_format(1, 'TXTZ', self.make_txtz(b'a test text'))
        fts = cache.enable_fts()
        self.wait_for_fts_to_finish(fts)
        check(id=1, book=1, format='TXTZ', searchable_text='a test text')
        # check changing the format but not the text doesnt cause a rescan
        cache.add_format(1, 'TXTZ', self.make_txtz(b'a test text', extra='xxx'))
        self.wait_for_fts_to_finish(fts)
        check(id=1, book=1, format='TXTZ', searchable_text='a test text')
        cache.close()

    def test_fts_triggers(self):
        cache = self.init_cache()
        fts = cache.enable_fts(start_pool=False)
        self.ae(fts.all_currently_dirty(), [(1, 'FMT1'), (1, 'FMT2'), (2, 'FMT1')])
        fts.dirty_existing()
        self.ae(fts.all_currently_dirty(), [(1, 'FMT1'), (1, 'FMT2'), (2, 'FMT1')])
        cache.remove_formats({2: ['FMT1']})
        self.ae(fts.all_currently_dirty(), [(1, 'FMT1'), (1, 'FMT2')])
        cache.remove_books((1,))
        self.ae(fts.all_currently_dirty(), [])
        cache.add_format(2, 'ADDED', BytesIO(b'data'))
        self.ae(fts.all_currently_dirty(), [(2, 'ADDED')])
        fts.clear_all_dirty()
        self.ae(fts.all_currently_dirty(), [])
        cache.add_format(2, 'ADDED', BytesIO(b'data2'))
        self.ae(fts.all_currently_dirty(), [(2, 'ADDED')])
        fts.add_text(2, 'ADDED', 'data2')
        self.ae(fts.all_currently_dirty(), [])
        cache.add_format(2, 'ADDED', BytesIO(b'data2'))
        self.ae(fts.all_currently_dirty(), [(2, 'ADDED')])
        fts.add_text(2, 'ADDED', 'data2')
        self.ae(fts.all_currently_dirty(), [])
        fts.dirty_existing()
        j = fts.get_next_fts_job()
        self.ae(j, (2, 'ADDED'))
        self.ae(j, fts.get_next_fts_job())
        fts.remove_dirty(*j)
        self.assertNotEqual(j, fts.get_next_fts_job())

    def test_fts_to_text(self):
        from calibre.ebooks.oeb.polish.parsing import parse
        html = '''
<html><body>
<div>first_para</div><p>second_para</p>
<p>some <i>itali</i>c t<!- c -->ext</p>
<div>nested<p>blocks</p></div>
</body></html>
'''
        root = parse(html)
        self.ae(tuple(html_to_text(root)), ('first_para\n\nsecond_para\n\nsome italic text\n\nnested\n\nblocks',))


def find_tests():
    import unittest
    return unittest.defaultTestLoader.loadTestsFromTestCase(FTSAPITest)


def run_tests():
    from calibre.utils.run_tests import run_tests
    run_tests(find_tests)
