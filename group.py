class RateGroup:
    def __init__(self, group, level, rateAgreements):
        self.grpLvl = group + '-' + level
        self.group = group
        self.level = level
        self.rateAgreements = rateAgreements
    def to_dict(self):
        return {"grpLvl": self.grpLvl, "group": self.group, "level": self.level, "rateAgreements" : [rateAgreenent.to_dict() for rateAgreenent in self.rateAgreements]}

class RateAgreement:
    def __init__(self, effectiveDate, rateStepsList):
        self.effectiveDate = effectiveDate
        self.rateStepsList = rateStepsList
    def to_dict(self):
        return {"effectiveDate": self.effectiveDate, "rateStepsList": [rateStep.to_dict() for rateStep in self.rateStepsList]}


class RateSteps:
    def __init__(self, step, amount):
        self.step = step
        self.amount = amount
    def to_dict(self):
        return {"step": self.step, "amount": self.amount}