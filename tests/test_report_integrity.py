import hashlib,tempfile,unittest
from pathlib import Path
from report_integrity import validate,ReportIntegrityError
class T(unittest.TestCase):
 def test_pass_and_fail(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'x.txt'; p.write_text('x'); h=hashlib.sha256(b'x').hexdigest()
   r={'task_id':'t','final_status':'EXPERT_TEAM_COMPLETED','judge_report':'ok','call_ledger':[1,2,3,4],'expert_outputs':['a','b','c'],'manifest':{'files':[{'path':'x.txt','sha256':h}]}}
   self.assertEqual(validate(r,Path(d))['status'],'PASS')
   r['expert_outputs'][0]=''
   with self.assertRaises(ReportIntegrityError): validate(r,Path(d))
if __name__=='__main__': unittest.main()
