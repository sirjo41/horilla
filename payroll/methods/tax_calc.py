"""
Module: payroll.tax_calc

This module contains a function for calculating the taxable amount for an employee
based on their contract details and income information.
"""

import datetime
import logging

from payroll.methods.methods import (
    compute_yearly_taxable_amount,
    convert_year_tax_to_period,
)
from payroll.methods.payslip_calc import (
    calculate_gross_pay,
    calculate_taxable_gross_pay,
)
from payroll.models.models import Contract
from payroll.models.tax_models import TaxBracket

logger = logging.getLogger(__name__)


def calculate_taxable_amount(**kwargs):
    """Calculate the taxable amount for a given employee within a specific period.

    Args:
        employee (int): The ID of the employee.
        start_date (datetime.date): The start date of the period.
        end_date (datetime.date): The end date of the period.
        basic_pay (float): The basic pay amount.

    Returns:
        float: The federal tax amount for the specified period.
    """

    employee_id = kwargs["employee"]
    start_date = kwargs["start_date"]
    end_date = kwargs["end_date"]
    basic_pay = kwargs["basic_pay"]

    # Fetch employee's active contract
    contract = Contract.objects.filter(
        employee_id=employee_id, contract_status="active"
    ).first()

    if not contract or not contract.filing_status:
        return 0

    filing = contract.filing_status
    federal_tax_for_period = 0

    tax_brackets = TaxBracket.objects.filter(filing_status_id=filing).order_by("min_income")
    num_days = (end_date - start_date).days + 1

    calculation_functions = {
        "taxable_gross_pay": calculate_taxable_gross_pay,
        "gross_pay": calculate_gross_pay,
    }

    based = filing.based_on
    if based in calculation_functions:
        calculation_function = calculation_functions[based]
        income = calculation_function(**kwargs)
        income = float(income[based])
    else:
        income = float(basic_pay)

    year = end_date.year
    total_days = (datetime.date(year, 12, 31) - datetime.date(year, 1, 1)).days + 1
    yearly_income = income / num_days * total_days
    yearly_income = compute_yearly_taxable_amount(income, yearly_income)
    yearly_income = round(yearly_income, 2)

    federal_tax = 0

    if filing and not filing.use_py:
        # Default DB-based tax bracket calculation
        brackets = [
            {
                "rate": item["tax_rate"],
                "min": item["min_income"],
                "max": min(item["max_income"], yearly_income),
            }
            for item in tax_brackets.values("tax_rate", "min_income", "max_income")
        ]

        filtered_brackets = []
        for bracket in brackets:
            if bracket["max"] > bracket["min"]:
                bracket["diff"] = bracket["max"] - bracket["min"]
                bracket["calculated_rate"] = (bracket["rate"] / 100) * bracket["diff"]
                filtered_brackets.append(bracket)
            else:
                break

        federal_tax = sum(bracket["calculated_rate"] for bracket in filtered_brackets)

    elif filing.use_py:
        # Use Python code-based custom tax logic
        code = filing.python_code.replace("print(", "pass_print(")
        pass_print_def = """
def pass_print(*args, **kwargs):
    return None
"""
        code = pass_print_def + code
        code = code.replace("  formated_result(", "#  formated_result(")
        local_vars = {}

        try:
            exec(code, {}, local_vars)

            gross_income = income
            marital_status = getattr(contract, "marital_status", "single")
            num_children = getattr(contract, "num_children", 0)

            federal_tax = local_vars["calcluate_federal_tax"](
                gross_income,
                marital_status=marital_status,
                num_children=num_children
            )
        except Exception as e:
            logger.error(f"Custom federal tax script error: {e}")
            federal_tax = 0

    # Prorate tax over period
    if federal_tax and (tax_brackets.exists() or filing.use_py):
        daily_tax = federal_tax / total_days
        federal_tax_for_period = daily_tax * num_days

    # Adjust final value for exact period (Horilla method)
    federal_tax_for_period = convert_year_tax_to_period(
        federal_tax_for_period=federal_tax_for_period,
        yearly_tax=federal_tax,
        total_days=total_days,
        start_date=start_date,
        end_date=end_date,
    )

    return federal_tax_for_period
