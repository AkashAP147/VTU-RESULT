"""
VTU CAPTCHA Bypass Module
=========================
A standalone CAPTCHA solver that uses two techniques:
1. A deep-learning model (Keras) trained on VTU CAPTCHAs (primary)
2. Tesseract OCR as a fallback

Usage:
    from captcha_bypass import CaptchaSolver

    solver = CaptchaSolver()

    # From a Selenium WebDriver element:
    captcha_text = solver.solve_from_element(driver, captcha_xpath)

    # From an image file:
    captcha_text = solver.solve_from_image("path/to/captcha.png")
"""

import os
import cv2
import numpy as np
import pickle
import pytesseract
from PIL import Image
from keras.models import load_model
from .helpers import resize_to_fit

# Resolve paths relative to this file
_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_MODEL_PATH = os.path.join(_MODULE_DIR, "model", "captcha_model.hdf5")
_LABELS_PATH = os.path.join(_MODULE_DIR, "model", "model_labels.dat")
_TEMP_DIR = os.path.join(_MODULE_DIR, "temp")


class CaptchaSolver:
    """
    Solves VTU-style CAPTCHAs using a trained Keras CNN model with
    Tesseract OCR as fallback.
    """

    def __init__(self, tesseract_cmd=None):
        """
        Initialize the solver by loading the trained model and labels.

        :param tesseract_cmd: Path to tesseract executable.
                              Defaults to 'C:\\Program Files\\Tesseract-OCR\\tesseract.exe' on Windows.
        """
        os.makedirs(_TEMP_DIR, exist_ok=True)

        # Load model labels
        with open(_LABELS_PATH, "rb") as f:
            self.label_binarizer = pickle.load(f)

        # Load trained neural network
        self.model = load_model(_MODEL_PATH)

        # Configure Tesseract
        if tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
        elif os.name == "nt":
            pytesseract.pytesseract.tesseract_cmd = (
                r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            )

    def _preprocess_image(self, image_path):
        """
        Clean the CAPTCHA image: remove noise lines and convert to
        black text on white background.

        :param image_path: path to the raw CAPTCHA image
        :return: path to the cleaned image
        """
        img = cv2.imread(image_path)

        # Remove gray noise lines
        lower = (102, 102, 102)
        upper = (125, 125, 125)
        mask = cv2.inRange(img, lower, upper)
        img[mask != 0] = [0, 0, 0]

        semi_path = os.path.join(_TEMP_DIR, "semisolved.png")
        cv2.imwrite(semi_path, img)

        # Convert non-black pixels to white
        pil_img = Image.open(semi_path)
        pixels = pil_img.load()
        for i in range(pil_img.size[0]):
            for j in range(pil_img.size[1]):
                if pixels[i, j] != (0, 0, 0):
                    pixels[i, j] = (255, 255, 255)

        solved_path = os.path.join(_TEMP_DIR, "solved.png")
        pil_img.save(solved_path)

        return solved_path

    def _solve_with_model(self, image_path):
        """
        Solve the CAPTCHA using the trained Keras CNN model.

        :param image_path: path to the preprocessed (solved) CAPTCHA image
        :return: predicted CAPTCHA text (may be fewer than 6 chars if extraction fails)
        """
        import imutils

        image = cv2.imread(image_path)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # Threshold to pure black and white
        thresh = cv2.threshold(image, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]

        # Find contours
        contours = cv2.findContours(thresh.copy(), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        contours = contours[1] if imutils.is_cv3() else contours[0]

        letter_image_regions = []

        for contour in contours:
            (x, y, w, h) = cv2.boundingRect(contour)

            if w < 10 and h < 10:
                pass
            elif 1.3 <= w / h < 1.38:
                half_width = int(w / 2)
                letter_image_regions.append((x, y, half_width, h))
                letter_image_regions.append((x + half_width, y, half_width, h))
            elif 1.44 < w / h < 1.52:
                half_width = int(w / 2)
                letter_image_regions.append((x, y, half_width, h))
                letter_image_regions.append((x + half_width, y, half_width, h))
            elif w / h > 1.6:
                half_width = int(w / 2)
                letter_image_regions.append((x, y, half_width, h))
                letter_image_regions.append((x + half_width, y, half_width, h))
            else:
                letter_image_regions.append((x, y, w, h))

        # Sort left-to-right
        letter_image_regions = sorted(letter_image_regions, key=lambda x: x[0])

        predictions = []

        for letter_bounding_box in letter_image_regions:
            x, y, w, h = letter_bounding_box
            x1, x2, y1, y2 = max(x - 2, 0), x + w + 2, max(y - 2, 0), y + h + 2

            letter_image = image[y1:y2, x1:x2]
            letter_image = resize_to_fit(letter_image, 50, 50)
            letter_image = np.expand_dims(letter_image, axis=2)
            letter_image = np.expand_dims(letter_image, axis=0)

            prediction = self.model.predict(letter_image, verbose=0)
            letter = self.label_binarizer.inverse_transform(prediction)[0]
            predictions.append(letter)

        captcha_text = "".join(predictions)

        # Trim to 6 characters if model over-predicted
        if len(captcha_text) > 6:
            captcha_text = captcha_text[:6]

        return captcha_text

    def _solve_with_tesseract(self, image_path):
        """
        Solve the CAPTCHA using Tesseract OCR (fallback method).

        :param image_path: path to the preprocessed CAPTCHA image
        :return: predicted CAPTCHA text or empty string on failure
        """
        try:
            img = cv2.imread(image_path)
            config = "-l eng --oem 1 --psm 3"
            text = pytesseract.image_to_string(img, config=config)
            return text.split("\n")[0]
        except Exception as e:
            print(f"⚠️ Tesseract fallback failed: {e}")
            return ""

    def solve_from_image(self, image_path):
        """
        Solve a CAPTCHA from an image file on disk.

        :param image_path: path to the raw CAPTCHA image
        :return: predicted CAPTCHA text (6 characters)
        """
        solved_path = self._preprocess_image(image_path)

        # Try ML model first
        captcha = self._solve_with_model(solved_path)

        # Fall back to Tesseract if model couldn't extract enough characters
        if len(captcha) < 6:
            captcha = self._solve_with_tesseract(solved_path)

        return captcha

    def solve_from_element(self, driver, captcha_xpath):
        """
        Solve a CAPTCHA directly from a Selenium WebDriver by screenshotting
        the CAPTCHA element.

        :param driver: Selenium WebDriver instance
        :param captcha_xpath: XPath to the CAPTCHA image element
        :return: predicted CAPTCHA text
        """
        from selenium.webdriver.common.by import By

        unsolved_path = os.path.join(_TEMP_DIR, "unsolved.png")
        element = driver.find_element(By.XPATH, captcha_xpath)
        element.screenshot(unsolved_path)

        return self.solve_from_image(unsolved_path)
