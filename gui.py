from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import *
from PyQt5 import QtCore, QtWidgets
import csv
import matplotlib.pyplot as plt
import sys
import serial
import time
from PyQt5.uic import loadUiType
import lifi_pb2
import io
import PIL.Image as Image
import bson

mainApp, _ = loadUiType('gui_main.ui')


class ReceiverWorker(QtCore.QThread):
    done = QtCore.pyqtSignal(bool)
    message = QtCore.pyqtSignal(str)

    def __init__(self, parent=None, receiver=None):
        super(ReceiverWorker, self).__init__(parent)
        self.txRxObj = receiver

    def run(self):
        while True:
            data = self.txRxObj.readall()
            if data:
                tReceived = time.time()
                self.done.emit(True)
                val = bson.decode(data)
                tSent = val['timestamp']
                msg = val['message']
                msgSize = val['messageSize']
                transmitTime = tReceived - tSent

                tmp = "\n\nTransmission time: {} seconds \nBytes sent: {}".format(transmitTime, msgSize)
                output = "\n************New data received*********\n" + msg + tmp
                self.message.emit(output)
                print(val)

                with open("log.csv", 'a', encoding='utf-8') as f:
                    f.write('\n' + str(msgSize) + ',' + str(transmitTime))
                print(output)

    def stop(self):
        self.terminate()
        print("Stopping Text Receiver worker ...")


class SenderWorker(QtCore.QThread):
    any_signal = QtCore.pyqtSignal(bool)
    statusSignal = QtCore.pyqtSignal(int)

    def __init__(self, parent=None, sender=None, message='', timestamp='Disabled', repeat=1):
        super(SenderWorker, self).__init__(parent)
        self.txRxObj = sender
        self.message = message
        self.timeStampEnabled = timestamp
        self.repeat = repeat

    def run(self):
        n = 1
        timeValue = time.time()
        msgSize = sys.getsizeof(self.message)
        output = {"message": self.message,
                  "timestamp": timeValue,
                  "messageSize": msgSize
                  }

        while n <= self.repeat:
            buffer = bson.encode(output)
            percentage = int(n/self.repeat * 100)
            print(percentage)
            self.statusSignal.emit(percentage)
            self.txRxObj.write(buffer)
            n += 1
            time.sleep(1)
            # self.txRxObj.write(bytes('', 'utf-8'))

        self.any_signal.emit(True)
        pass

    def stop(self):
        self.terminate()


class ImageWorker(QtCore.QThread):
    any_signal = QtCore.pyqtSignal(bool)

    def __init__(self, parent=None, sender=None, path=''):
        super(ImageWorker, self).__init__(parent)
        self.txRxObj = sender
        self.path = path

    def run(self):
        print('Sender started')
        with open(self.path, "rb") as f:
            val = bytearray(f.read())
        self.txRxObj.write(val)
        self.any_signal.emit(True)

    def stop(self):
        self.terminate()
        print("Stopping Image sender thread ...")


class ImageReceiverWorker(QtCore.QThread):
    done = QtCore.pyqtSignal(bool)
    stopped = QtCore.pyqtSignal(bool)
    fname = QtCore.pyqtSignal(str)

    def __init__(self, parent=None, receiver=None):
        super(ImageReceiverWorker, self).__init__(parent)
        self.receiver = receiver

    def run(self):
        print("Receiver Started")
        while True:
            val = self.receiver.readall()
            if val:
                image = Image.open(io.BytesIO(val))
                imgFormat = image.format
                filename = "receivedImage" + "." + imgFormat
                image.save(filename)
                self.done.emit(True)
                self.fname.emit(filename)
                print(val)

    def stop(self):
        print("Stopping image receiver thread ...")
        self.terminate()
        self.stopped.emit(True)


class MainWindow(QMainWindow, mainApp):
    connectionFlag = False
    imageReceiverRunning = False

    def __init__(self):
        QMainWindow.__init__(self)
        self.setupUi(self)
        self.setWindowTitle("LiFi Serial")
        self.initialConfig = False
        self.echoMode = self.echo.isChecked()
        self.remoteMode = self.remote.isChecked()
        self.txRxObj = None
        self.sendersPort = self.senderPort.currentText()
        self.parity = self.parityC.currentText()
        self.stopbit = int(self.stopbitC.currentText())
        self.bitspersec = int(self.bitspersecC.currentText())
        self.databits = int(self.databitsC.currentText())
        self.timeout = float(self.timeoutC.text())
        self.edc = self.edcC.currentText()
        self.message = lifi_pb2.data()
        # set the first page as the configuration page on startup
        self.tabWidget.setCurrentIndex(1)

        # call the button handler
        self.buttonHandler()
        self.setStatusBar(QStatusBar(self))

        self.createPlotFile()
        # self.showImage()

        # Multithreading
        self.thread = {}

    def buttonHandler(self):
        # set the config flag so that the application can only send when initial configuration has been done
        self.buttonBox.accepted.connect(self.settingsHandler)
        self.echo.toggled.connect(lambda: self.radioButtons('sender'))
        self.remote.toggled.connect(lambda: self.radioButtons('receiver'))
        self.echo.setChecked(True)
        self.start.clicked.connect(self.sendMessage)
        self.plotData.triggered.connect(self.plotter)
        self.save.clicked.connect(self.saveSentData)
        self.clearInput.clicked.connect(self.clearInputFunc)
        self.actionOpen.triggered.connect(self.loadFile)
        self.actionSend_Image.triggered.connect(self.loadImage)
        self.actionListen.triggered.connect(self.receiveImage)
        self.actionStop_Listening.triggered.connect(self.stopListening)
        self.closePort.clicked.connect(self.closePorts)
        self.stop.clicked.connect(self.stopThread)
        self.actionClear_Image.triggered.connect(self.clearImage)

    def settingsHandler(self):
        # In image mode, check if image receiver worker is running. if yes, stop it
        print(MainWindow.imageReceiverRunning)
        self.stopThread()
        MainWindow.imageReceiverRunning = False

        # close all existing connections
        if MainWindow.connectionFlag:
            self.txRxObj.close()

        self.sendersPort = self.senderPort.currentText()
        self.parity = self.parityC.currentText()
        self.stopbit = int(self.stopbitC.currentText())
        self.bitspersec = int(self.bitspersecC.currentText())
        self.databits = int(self.databitsC.currentText())
        self.timeout = float(self.timeoutC.text())
        self.edc = self.edcC.currentText()

        try:
            trRxObject = serial.Serial(port=self.sendersPort, baudrate=self.bitspersec, timeout=self.timeout,
                                       parity=self.parity,
                                       stopbits=self.stopbit,
                                       bytesize=self.databits)
            self.senderStatus.setText(
                "Connected: Port-{} Parity-{} Stop-{} Baudrate-{}".format(self.sendersPort, self.parity, self.stopbit,
                                                                          self.bitspersec))
            self.data_inner_frame.setEnabled(True)
            self.terminalWindow.setReadOnly(False)
            self.txRxObj = trRxObject
            self.frame_4.setEnabled(True)

            self.initialConfig = True
            print('Configuration saved')

            # Set the connection flag. This can be done by checking for active ports but its faster this way IMO
            MainWindow.connectionFlag = True
            if self.remoteMode:
                self.receiveMessage()

        except serial.SerialException:
            MainWindow.connectionFlag = False
            self.data_inner_frame.setEnabled(False)
            self.senderStatus.setText("Port Closed")
            self.statusBar().showMessage("Cannot open port", 2000)
            print('Bad Config params')

    def sendMessage(self):
        data = self.terminalWindow.toPlainText()
        timeStampEnabled = self.timestamps.currentText()
        numberOfRepeats = int(self.repeats.value())
        self.progressBarValue.setMaximum(100)
        self.thread[1] = SenderWorker(sender=self.txRxObj, message=data, timestamp=timeStampEnabled,
                                      repeat=numberOfRepeats)
        self.thread[1].start()
        self.nodeStatus.setText("Mode: Text Tx, Sending")
        self.thread[1].statusSignal.connect(self.notification)
        self.thread[1].any_signal.connect(lambda: self.showDialog("information", "Success", "File sent successfully"))
        self.thread[1].any_signal.connect(lambda: self.nodeStatus.setText("Mode: Text Tx, Done"))

    def receiveMessage(self):
        self.thread[2] = ReceiverWorker(receiver=self.txRxObj)
        self.thread[2].start()
        self.nodeStatus.setText("Mode: Text Rx, Receiving")
        self.thread[2].message.connect(self.displayReceivedMessage)

    def notification(self, msg):
        self.progressBarValue.setValue(msg)
        print("Progress value ",msg)

    def displayReceivedMessage(self, msg):
        if msg:
            self.terminalWindow.append(msg)

    def radioButtons(self, name):
        radioBtn = self.sender()
        if name == 'sender':
            # self.receiverPort.setEnabled(True)
            self.echoMode = True
            self.remoteMode = False
            self.terminalWindow.setReadOnly(False)
            self.repeats.setEnabled(True)
            self.start.setEnabled(True)
        elif name == 'receiver' and radioBtn.isChecked():
            self.remoteMode = True
            self.echoMode = False
            self.terminalWindow.setReadOnly(True)
            self.repeats.setEnabled(False)
            self.start.setEnabled(False)

    def closePorts(self):
        self.txRxObj.close()
        self.senderStatus.setText("Port Closed")
        self.frame_4.setEnabled(False)

    @staticmethod
    def showDialog(error_type='information', text='Success', message='c'):
        msg = QMessageBox()
        if error_type == 'information':
            msg.setIcon(QMessageBox.Information)
        if error_type == 'error':
            msg.setIcon(QMessageBox.Warning)
        msg.setText(text)
        msg.setInformativeText(message)
        msg.setWindowTitle("Information")
        msg.setStandardButtons(QMessageBox.Ok)
        retVal = msg.exec_()
        if retVal == QMessageBox.Ok:
            pass

    @staticmethod
    def plotter(self):
        timestamps = []
        byteSizes = []
        with open('log.csv', 'r') as f:
            plots = csv.DictReader(f, delimiter=',')
            for row in plots:
                byteSizes.append((float(row['Byte_Size'])))
                timestamps.append(float(row['Transmission_time']))

        plt.plot(byteSizes, timestamps)
        plt.grid()
        plt.xlabel('Number of bytes')
        plt.ylabel('Time')
        plt.title('Number of Bytes sent vs Transmission time')
        plt.show()

    def createPlotFile(self):
        with open('log.csv', 'w') as f:
            f.write('Byte_Size' + ',' + 'Transmission_time')

    def saveSentData(self):
        data = self.terminalWindow.toPlainText()
        with open('DataSent', 'a') as f:
            f.write('\n' + data)
        MainWindow.showDialog(text="Successful", message='Message Saved')

    def clearInputFunc(self):
        print("I got here")
        data = self.terminalWindow.toPlainText()
        # print(data)
        print(f'Terminal value %s', data)
        if data:
            self.terminalWindow.setPlainText('')
        else:
            pass

    def loadFile(self):
        fname = QFileDialog.getOpenFileName(self, 'Open file', 'c:\\', "Text files (*.txt)")[0]

        if fname != '':
            with open(fname, 'r') as f:
                data = f.read()
                self.terminalWindow.setPlainText(str(data))
        else:
            MainWindow.showDialog(error_type="error", message="Error", text='No file was selected')

    def loadImage(self):
        fname = QFileDialog.getOpenFileName(self, 'Open Image', 'c:\\', "Text files (*.jpg, *.png)")[0]

        if fname != '' and MainWindow.connectionFlag:
            self.thread[3] = ImageWorker(sender=self.txRxObj, path=fname)
            self.thread[3].start()
            self.statusBar().showMessage("Sending", 4000)
            self.nodeStatus.setText("Mode: Image Tx, Sending")
            self.thread[3].any_signal.connect(
                lambda: self.showDialog("information", "Success", "File sent successfully"))
            self.thread[3].any_signal.connect(
                lambda: self.nodeStatus.setText("Mode: Image Tx, Done"))

        else:
            MainWindow.showDialog(error_type="error", message="Error", text='No file was selected')

    def stopThread(self):
        # you can iterate through the thread dictionary and stop them
        if len(self.thread.keys()):
            for thr in self.thread.keys():
                self.thread[thr].stop()
        else:
            MainWindow.showDialog("error", "Warning", "No Active Threads")

    def showImage(self,fname):
        # receives signal about the file name of the received file and uploads it to label
        pixmap = QPixmap(fname)
        self.imageHolder.setPixmap(pixmap)

    def receiveImage(self):
        # before listening for messages ensure that the receive image mode was checked, else throw a warning
        if self.imageMode.isChecked():
            self.thread[4] = ImageReceiverWorker(receiver=self.txRxObj)
            self.thread[4].start()
            MainWindow.imageReceiverRunning = True
            self.nodeStatus.setText("Mode: Image Rx, Listening")
            self.thread[4].fname.connect(self.showImage)
            self.thread[4].done.connect(self.doneReceiving)
            self.thread[4].stopped.connect(lambda :self.nodeStatus.setText("Mode: Image Rx, Stopped"))
        else:
            MainWindow.showDialog("error", "Warning", "You cannot receive an image in text mode")

    def stopListening(self):
        if MainWindow.imageReceiverRunning:
            self.thread[4].stop()
            MainWindow.imageReceiverRunning = False

    def clearImage(self):
        self.imageHolder.clear()
        self.imageHolder.setText("No image to display")

    def doneReceiving(self):
        self.nodeStatus.setText("Mode: Image Rx, Done")
        self.tabWidget.setCurrentIndex(2)


def main():
    app = QtWidgets.QApplication(sys.argv)
    prog = MainWindow()
    prog.show()
    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
