#!/usr/bin/python2
import decimal

import sys
sys.path.append("../lib")
import namerpc

import antpyshared

debug = False
if __name__ == '__main__':
    debug = True
    import pprint

class AntpyCore(object):
    def __init__(self, rpc_call):
        self.rpc_call = rpc_call  # should also unlock if necessary
    def get_available_balance(self):
        return self.rpc_call("getbalance") - antpyshared.TXFEENMC
    def create_offer(self, name, bidNmc):

        # check name
        try:
            nameData = self.rpc_call("name_show", [name])
        except namerpc.WalletError as e:
            if e.code == -4:  # failed to read from db
                raise antpyshared.NameDoesNotExistError
            else:
                raise

        # inputs (select uses satoshis)
        bidSatoshis = antpyshared.to_satoshis(bidNmc)
        unspent = self.rpc_call("listunspent")
        for u in unspent:
            u["satoshis"] = antpyshared.to_satoshis(u["amount"])
        inputs = antpyshared.select(unspent, bidSatoshis + antpyshared.TXFEESATOSHIS)  # !!! check for too many inputs
        sumInputsNmc = sum([i["amount"] for i in inputs])

        # outputs
        outputs = {nameData["address"] : bidNmc}  # Bitcoin client will do proper rounding

        changeNmc = sumInputsNmc - bidNmc - antpyshared.TXFEENMC
        if antpyshared.to_satoshis(changeNmc) != 0:
            buyerChangeAddress = self.rpc_call("getnewaddress")
            outputs[buyerChangeAddress] = changeNmc

        # name_op
        buyerNameAddress = self.rpc_call("getnewaddress")
        nameOp = {"op" : "name_update",
                  "name" : name,
                  "value" : "",  # name data !!! make changeable by user
                  "address" : buyerNameAddress}
        inputs.append(nameData)

        # assembly
        if debug:
            print "inputs--------------------"
            pprint.pprint(inputs)
            print "outputs-------------------"
            pprint.pprint(outputs)
            print "nameop--------------------"
            pprint.pprint(nameOp)
        rawTx = self.rpc_call("createrawtransaction", [inputs, outputs, nameOp])

        # fee check (not easily possible - buyer/seller need to verify their output values)
        if debug:
            tx = self.rpc_call("decoderawtransaction", [rawTx])
            print "tx------------------------"
            pprint.pprint(tx)

        # buyer sign
        r = self.rpc_call("signrawtransaction", [rawTx])
        rawTx = r["hex"]

        # buyer check
        tx = self.rpc_call("decoderawtransaction", [rawTx])
        D = antpyshared.analyze_tx(tx, self.rpc_call, seller=False)

        D["rawTx"] = rawTx  # only store after it has been checked

        assert name == D["name"]

        if debug:
            print D
        return D

    def seller_decode(self, hexTx):
        hexTx = hexTx.strip().replace("\n", "").replace("\r", "").replace(" ", "")

        try:
            int(hexTx, 16)
        except ValueError:
            raise

        try:
            tx = self.rpc_call("decoderawtransaction", [hexTx])
        except namerpc.RpcError as e:
            if (e.args[0]["error"]["code"] == -8 or e.args[0]["error"]["code"] == -22):  # decode errors
                raise Exception("decode error")
            raise

        # analyze tx
        self.D = antpyshared.analyze_tx(tx, self.rpc_call, seller=True)
        nameList = self.rpc_call("name_list", [self.D["name"]])
        if nameList == []:
            raise Exception("Name not in wallet: " + str(self.D["name"]))
        if nameList[0].has_key("transferred") and nameList[0]["transferred"]:
            raise Exception("Name has already been transferred: " + str(self.D["name"]))
        self.D["sellerAddress"] = nameList[0]["address"]
        self.D["hexTx"] = hexTx  # only store after it has been checked        
        self.D["vinOrig"] = tx["vin"]
        return self.D
    
    def seller_sign(self):
        # seller sign
        privKey = self.rpc_call("dumpprivkey", [self.D["sellerAddress"]])
        r = self.rpc_call("signrawtransaction", [self.D["hexTx"], [], [privKey]])
        del privKey  # gc.collect? not that it really works

        if r["complete"] != True:
            raise Exception("Could not complete transaction.")

        hexTx = r["hex"]
        tx = self.rpc_call("decoderawtransaction", [hexTx])
        if debug:
            print "signed---------------------------------------------------"
            pprint.pprint(tx)

        # verify tx
        vinDiff = []
        for v in tx["vin"]:
            if not v in self.D["vinOrig"]:
                vinDiff.append(v)
        if not vinDiff:
            raise Exception("All inputs already signed.")
        if len(vinDiff) != 1:
            raise Exception("Signed more than one input. Bailing due to fraud potential.")

        pTx = self.rpc_call("getrawtransaction", [vinDiff[0]["txid"], 1])

        # check value of signed prevOut - this fails if the address holding the name is "loaded" with more than NAMENEWFEENMC
        value = pTx["vout"][vinDiff[0]["vout"]]["value"]
        print type(value)
        assert type(value) == decimal.Decimal
        if value != antpyshared.NAMENEWFEENMC:
            raise Exception("Signed wrong input value? (" + str(vinDiff[0]["value"]) + "NMC). Bailing due to fraud potential.")

        # verify name (necessary?)
        try:
            prevName = antpyshared.get_name(pTx["vout"])
        except IndexError:
            raise Exception("Multiple names in previous tx. Currently not supported.")
        if prevName != self.D["name"]:
            raise Exception("Wrong name in previous tx: " + str(prevName) + " Bailing due to fraud potential.")
    
        self.D["hexTxFinal"] = hexTx  # only store after it has been checked

    def seller_broadcast(self):
        return self.rpc_call("sendrawtransaction", [self.D["hexTxFinal"]])


if __name__ == "__main__":
    debug = True
    rpc = namerpc.CoinRpc(connectionType="client")
    #unlockedWallet = antpyshared.UnlockWallet(rpc)  # gui

    def unlock():
        print "unlock wallet <enter>"
        raw_input()
    apc = AntpyCore(rpc.call)

    apc.create_offer('d/nx', decimal.Decimal('1'))
    
    print "available balance:", apc.get_available_balance()
