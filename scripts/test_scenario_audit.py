#!/usr/bin/env python3
"""Offline forward-only scenario audit tests."""
import unittest
from datetime import datetime,timedelta,timezone
from evaluate_scenarios import process

UTC=timezone.utc
DATE=datetime(2026,11,7,8,0,tzinfo=UTC)

def snap(at=DATE,price=1.0):
    return {'updated_at':at.isoformat(),'coins':[
        {'id':'a','symbol':'A','current_price':price},
        {'id':'b','symbol':'B','current_price':price*2},
        {'id':'c','symbol':'C','current_price':price*3}]}

class ScenarioAuditTests(unittest.TestCase):
    def test_first_run_only_issues_no_outcomes(self):
        x=process(snap(),{'records':[]},DATE)
        self.assertEqual(x['summary']['issued'],6)
        self.assertEqual(x['summary']['evaluated'],0)
        self.assertTrue(all(r['status']=='PENDING' for r in x['records']))
        self.assertTrue(all(r['security_decision']=='SKIP' for r in x['records']))

    def test_repeat_same_day_does_not_duplicate(self):
        first=process(snap(),{'records':[]},DATE)
        later=DATE+timedelta(minutes=30)
        again=process(snap(later),first,later)
        self.assertEqual(again['summary']['issued'],6)

    def test_evaluate_only_after_maturity(self):
        first=process(snap(),{'records':[]},DATE)
        almost=DATE+timedelta(days=29,hours=23)
        pending=process(snap(almost,price=1.2),first,almost)
        self.assertEqual(pending['summary']['evaluated'],0)
        due=DATE+timedelta(days=30,minutes=10)
        outcome=process(snap(due,price=1.2),pending,due)
        self.assertEqual(outcome['summary']['evaluated'],3)
        self.assertEqual(outcome['summary']['inside_range'],3)
        self.assertEqual(outcome['summary']['pending'],9)  # Monday issues a fresh weekly cohort

    def test_missing_outcome_marked_missed_not_invented(self):
        first=process(snap(),{'records':[]},DATE)
        late=DATE+timedelta(days=33)
        r=process(snap(late),first,late)
        self.assertEqual(r['summary']['missed'],3)
        self.assertEqual(r['summary']['evaluated'],0)

    def test_runner_clock_cannot_evaluate_early_market_snapshot(self):
        first=process(snap(),{'records':[]},DATE)
        due=DATE+timedelta(days=30)
        observed=due-timedelta(minutes=10)
        run_at=due+timedelta(minutes=2)
        result=process(snap(observed,price=2),first,run_at)
        self.assertEqual(result['summary']['evaluated'],0)
        self.assertEqual(result['summary']['pending'],6)

    def test_stale_price_rejected(self):
        with self.assertRaises(ValueError):
            process(snap(DATE-timedelta(hours=8)),{'records':[]},DATE)

    def test_resolved_outcome_never_rewritten(self):
        first=process(snap(),{'records':[]},DATE)
        due=DATE+timedelta(days=30,minutes=10)
        completed=process(snap(due,price=1.1),first,due)
        same=process(snap(due+timedelta(minutes=20),price=5),completed,due+timedelta(minutes=20))
        self.assertEqual(same['summary']['evaluated'],3)
        self.assertEqual([x.get('actual_usd') for x in same['records'] if x['status']=='EVALUATED'],
                         [x.get('actual_usd') for x in completed['records'] if x['status']=='EVALUATED'])

if __name__=='__main__':
    unittest.main()
