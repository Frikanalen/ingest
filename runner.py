import logging
import os
import subprocess


class Runner(object):
    @classmethod
    def run(cls, cmd, filepath=None, reprocess=False):
        logging.info("Running: %s", " ".join(cmd))
        if filepath:
            os.makedirs(os.path.dirname(filepath), exist_ok=True)
            if os.path.exists(filepath):
                if reprocess:
                    logging.info("Deleting file for reprocessing: %s", filepath)
                    os.remove(filepath)
                else:
                    logging.info("SKIP already existing file: %s", filepath)
                    return
        output = ""
        try:
            output = subprocess.check_output(cmd, stderr=subprocess.STDOUT)
        except subprocess.CalledProcessError as e:
            output = e.output
            raise e
        finally:
            logging.debug(output.decode("utf-8"))
