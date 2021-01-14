class RateGroup:
    def __init__(self, group, level, grpLvl, rateAgreements):
        self.grpLvl = grpLvl
        self.group = group
        self.level = level
        self.rateAgreements = rateAgreements

class RateAgreement:
    def __init__(self, effectiveDate, rateStepsList):
        self.effectiveDate = effectiveDate
        self.rateStepsList = rateStepsList

class RateSteps:
    def __init__(self, step, amount):
        self.step = step
        self.amount = amount