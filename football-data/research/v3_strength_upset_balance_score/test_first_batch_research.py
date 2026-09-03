from __future__ import annotations
import importlib.util, math, pathlib, sys, unittest
P=pathlib.Path(__file__).with_name('first_batch_research.py')
s=importlib.util.spec_from_file_location('fbr',P);m=importlib.util.module_from_spec(s);sys.modules['fbr']=m;s.loader.exec_module(m)
def base_matrix():
    a=[]
    for i in range(15):
        row=[]
        for j in range(15):row.append(math.exp(-1.4)*1.4**i/math.factorial(i)*math.exp(-1.1)*1.1**j/math.factorial(j))
        a.append(row)
    z=sum(map(sum,a));return [[x/z for x in r] for r in a]
class T(unittest.TestCase):
    def test_integrate_normalized(self):
        q=m.integrate(base_matrix());self.assertAlmostEqual(sum(q),1.0,12)
    def test_iproject_exact_target(self):
        q=[.52,.27,.21];x=m.iproject_regions(base_matrix(),q);self.assertTrue(m.matrix_valid(x));self.assertLess(max(abs(a-b) for a,b in zip(m.integrate(x),q)),1e-12)
    def test_score_tilt_preserves_1x2(self):
        x=base_matrix();q=m.integrate(x);z=m.score_tilt(x,.08,-.04);self.assertTrue(m.matrix_valid(z));self.assertLess(max(abs(a-b) for a,b in zip(m.integrate(z),q)),1e-12)
    def test_orientation_roundtrip(self):
        f=[.2,.25,.55];v=[.22,.26,.52];s,w,q=m.oriented(v,f);self.assertEqual((s,w),(2,0));self.assertEqual(m.map_hda(q,s),v)
    def test_multinomial_deterministic(self):
        rows=[]
        for i in range(60):
            x=[1.0,(i%5)/4,((i*3)%7)/6];q=[.45,.30,.25];y=0 if i%3==0 else 1 if i%3==1 else 2;rows.append({'x':x,'q':q,'y':y})
        a=m.fit_multinomial(rows,'x','q','y',8.0);b=m.fit_multinomial(rows,'x','q','y',8.0);self.assertEqual(a,b)
    def test_region_scaling_has_zero_conditional_change(self):
        x=base_matrix();q=m.integrate(x);target=[q[0]+.02,q[1]-.01,q[2]-.01];z=m.iproject_regions(x,target)
        for i,j in [(0,0),(1,0),(0,1),(2,1),(1,2)]:
            reg=0 if i>j else 1 if i==j else 2
            self.assertAlmostEqual(x[i][j]/q[reg],z[i][j]/target[reg],12)
if __name__=='__main__':unittest.main()
