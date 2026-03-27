"""
VTU CAPTCHA Bypass Module
=========================
Solves VTU result website CAPTCHAs using a trained Keras CNN model
with Tesseract OCR as fallback.

Usage:
    from captcha_bypass import CaptchaSolver

    solver = CaptchaSolver()
    text = solver.solve_from_image("captcha.png")
    # or with Selenium:
    text = solver.solve_from_element(driver, captcha_xpath)
"""

from .captcha_solver import CaptchaSolver

__all__ = ["CaptchaSolver"]
