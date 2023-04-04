"""Setup handlers for basic logging"""

import logging


def get_logger(loggername: str) -> logging.Logger:
    """Either create a new logger or gets an existing logger for a given name

    :param str loggername: user defined unique ID of the logger
    :return logging.Logger: logging handler
    """
    return logging.getLogger(loggername)


def configure_file_logging(loggername: str, loglevel: str = "debug",
                      logfilepath: str = "/tmp/funkmapper.log"):
    """Sets up a basic logging configuration using a watched file handler

    :param str loggername: user defined unique ID of the logger
    :param str loglevel: user defined logging level, defaults to "debug"
    :param str logfilepath: the absolute path of the logfile, defaults to "/tmp/funkmapper.log"
    """
    formatter = logging.Formatter("%(asctime)s {%(pathname)s:%(lineno)d} "
                                  "[%(funcName)s] %(levelname)s - %(message)s")

    # get/get a logger
    logger = get_logger(loggername)

    # remove logger if it already exits to prevent dual write
    for handler in logger.handlers:
        logger.removeHandler(handler)
        handler.close()  # remove the handler (and the files)

    # Write to file
    logfile_handler = logging.handlers.WatchedFileHandler(logfilepath)
    logfile_handler.setFormatter(formatter)

    # set logging level
    logger.setLevel(logging.getLevelName(loglevel.upper()))
    logger.addHandler(logfile_handler)
