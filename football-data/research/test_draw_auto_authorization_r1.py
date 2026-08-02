#!/usr/bin/env python3
from __future__ import annotations
import unittest
from validate_draw_auto_authorization_r1 import canonical_sha, validate_payload
class AuthorizationTests(unittest.TestCase):
    def objects(self):
        spec={'budget':{'maximum_candidates':200}}
        item={'path':'x','git_blob_sha':'a'*40}
        identity={'files':{'engine':item},'authorization_required_bindings':['engine']}
        auth={'schema_version':'DRAW-AUTO-RESEARCH-AUTHORIZATION-R1.4','status':'AUTHORIZED_VIEWED_DEVELOPMENT_AUTO_RESEARCH','user_authorization_record':'rec0WJJzXiuDvAqSb','data_status':'VIEWED_DEVELOPMENT_DATA','formal_weight':0,'spec_canonical_sha256':canonical_sha(spec),'identity_canonical_sha256':canonical_sha(identity),'identity_git_blob_sha':'b'*40,'bindings':{'engine':item}}
        return spec,identity,auth
    def test_complete_binding_passes(self):
        s,i,a=self.objects();self.assertEqual(validate_payload(a,s,i,'b'*40)['status'],'PASS_AUTHORIZATION_BINDINGS_ZERO_LABEL')
    def test_tampered_binding_fails(self):
        s,i,a=self.objects();a['bindings']['engine']={'path':'x','git_blob_sha':'c'*40}
        with self.assertRaises(ValueError): validate_payload(a,s,i,'b'*40)
if __name__=='__main__': unittest.main(verbosity=2)
