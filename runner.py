import logging
import subprocess


class Runner(object):
    @classmethod
    def run(cls, command: str):
        logging.info("Running: %s", command)

        output = ""
        try:
            output = subprocess.check_output(command, stderr=subprocess.STDOUT, shell=True)
        except subprocess.CalledProcessError as e:
            output = e.output
            raise e
        finally:
            logging.debug(output)
