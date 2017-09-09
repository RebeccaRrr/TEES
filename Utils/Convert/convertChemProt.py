import os, sys
import shutil
import csv
import codecs
from collections import defaultdict
from theano.sandbox.cuda.basic_ops import row
try:
    import cElementTree as ET
except ImportError:
    import xml.etree.cElementTree as ET
thisPath = os.path.dirname(os.path.abspath(__file__))
sys.path.append(os.path.abspath(os.path.join(thisPath,"../..")))
import Utils.Range as Range
import Utils.ElementTreeUtils as ETUtils
import Utils.Settings as Settings
import Utils
import tempfile
from collections import OrderedDict

def UnicodeDictReader(utf8_data, **kwargs):
    csv_reader = csv.DictReader(utf8_data, **kwargs)
    for row in csv_reader:
        yield {unicode(key, 'utf-8'):unicode(value, 'utf-8') for key, value in row.iteritems()}

def downloadFile(url, downloadDir=None, extractDir=None, clear=False):
    if downloadDir == None:
        downloadDir = os.path.join(Settings.DATAPATH, "corpora/download")
    downloaded = Utils.Download.download(url, downloadDir, clear=clear)
    print >> sys.stderr, "Extracting package", downloaded
    subPath = os.path.basename(downloaded).rsplit(".", 1)[0] + "/"
    extracted = Utils.Download.extractPackage(downloaded, extractDir) #, subPath)
    assert subPath in extracted
    return os.path.normpath(os.path.join(extractDir, subPath))

def convertChemProt(inDirs=None, setNames=None, outPath=None, downloadDir=None, extractDir=None, redownload=False, debug=False):
    tempDir = None
    if inDirs == None:
        print >> sys.stderr, "---------------", "Downloading ChemProt files", "---------------"
        if extractDir == None:
            tempDir = tempfile.mkdtemp()
        inDirs = []
        for setName in ("TRAIN", "DEVEL", "TEST"):
            if Settings.URL["CP17_" + setName] != None:
                currentExtractDir = extractDir if extractDir else tempDir
                currentExtractDir = os.path.join(currentExtractDir, setName.lower())
                inDirs.append(downloadFile(Settings.URL["CP17_" + setName], downloadDir, currentExtractDir, redownload))
    print >> sys.stderr, "Reading ChemProt corpus from input", inDirs, "using dataset mapping", setNames
    dataSets = OrderedDict()
    for inDir in inDirs:
        print >> sys.stderr, "Reading input directory", inDir
        filenames = os.listdir(inDir)
        filetypes = ["_abstracts.tsv", "_entities.tsv", "_relations.tsv"]
        # Collect the file paths for the data types
        dirDataSets = set()
        for filename in filenames:
            if not (filename.endswith(".tsv") and any([filename.endswith(x) for x in filetypes])):
                continue
            dataSetId, dataType = filename.rsplit("_", 1)
            if setNames != None:
                dataSetId = setNames.get(dataSetId, dataSetId)
            dirDataSets.add(dataSetId)
            dataType = dataType.split(".")[0]
            if dataSetId not in dataSets:
                dataSets[dataSetId] = {}
            assert dataType not in dataSets[dataSetId]
            dataSets[dataSetId][dataType] = os.path.join(inDir, filename)
        print >> sys.stderr, "Found ChemProt datasets", list(dirDataSets), "at", inDir
    print >> sys.stderr, "Read datasets:", dataSets.keys()
    # Build the Interaction XML
    print >> sys.stderr, "Converting to Interaction XML"
    corpusName = "CP17"
    corpus = ET.Element("corpus", {"source":corpusName})
    counts = defaultdict(int)
    docById = {}
    entityById = {}
    docsWithErrors = set()
    for dataSetId in sorted(dataSets.keys()):
        dataSet = dataSets[dataSetId]
        counts["sets"] += 1
        with open(dataSet["abstracts"], "rt") as f:
            for row in UnicodeDictReader(f, delimiter="\t", fieldnames=["id", "title", "abstract"], quoting=csv.QUOTE_NONE):
                document = ET.SubElement(corpus, "document", {"id":corpusName + ".d" + str(counts["documents"]), "origId":row["id"], "set":dataSetId})
                document.set("text", row["title"] + "\t" + row["abstract"])
                counts["documents"] += 1
                assert document.get("origId") not in docById
                docById[document.get("origId")] = document
        with open(dataSet["entities"], "rt") as f:
            for row in UnicodeDictReader(f, delimiter="\t", fieldnames=["docId", "id", "type", "begin", "end", "text"]):
                document = docById[row["docId"]]
                assert row["type"] in ("CHEMICAL", "GENE-Y", "GENE-N")
                offset = (int(row["begin"]), int(row["end"]))
                docSpan = document.get("text")[offset[0]:offset[1]]
                if docSpan == row["text"]:
                    entity = ET.SubElement(document, "entity", {"id":document.get("id") + ".e" + str(len([x for x in document.findall("entity")]))})
                    entity.set("given", "True")
                    entity.set("origId", row["id"])
                    entity.set("type", row["type"].split("-")[0])
                    entity.set("normalized", "True" if row["type"].endswith("-Y") else "False")
                    entity.set("charOffset", Range.tuplesToCharOffset((offset[0], offset[1])))
                    entity.set("text", row["text"])
                    if row["docId"] not in entityById:
                        entityById[row["docId"]] = {}
                    assert entity.get("origId") not in entityById[row["docId"]]
                    entityById[row["docId"]][entity.get("origId")] = entity
                    counts["entities"] += 1
                else:
                    print >> sys.stderr, "Alignment error in document", row["docId"], (offset, docSpan, row)
                    counts["entities-error"] += 1
                    docsWithErrors.add(row["docId"])
        with open(dataSet["relations"], "rt") as f:
            for row in UnicodeDictReader(f, delimiter="\t", fieldnames=["docId", "group", "groupEval", "type", "arg1", "arg2"]):
                for argId in ("1", "2"):
                    assert row["arg" + argId].startswith("Arg" + argId + ":")
                    row["arg" + argId] = row["arg" + argId][5:]
                document = docById[row["docId"]]
                e1 = entityById[row["docId"]].get(row["arg1"])
                e2 = entityById[row["docId"]].get(row["arg2"])
                if e1 != None and e2 != None:
                    interaction = ET.SubElement(document, "interaction", {"id":document.get("id") + ".i" + str(len([x for x in document.findall("interaction")]))})
                    interaction.set("directed", "True")
                    interaction.set("type", row["group"])
                    interaction.set("relType", row["type"])
                    row["groupEval"] = row["groupEval"].strip()
                    assert row["groupEval"] in ("Y", "N")
                    interaction.set("evaluated", "True" if row["groupEval"] == "Y" else "False")
                    interaction.set("e1", e1.get("id"))
                    interaction.set("e2", e2.get("id"))
                    counts["interactions"] += 1
                else:
                    counts["interaction-error"] += 1
                    docsWithErrors.add(row["docId"])
    if len(docsWithErrors) > 0:
        counts["documents-with-errors"] = len(docsWithErrors)
    print >> sys.stderr, "ChemProt conversion:", dict(counts)
    if tempDir != None and not debug:
        print >> sys.stderr, "Removing temporary directory", tempDir
        shutil.rmtree(tempDir)
    if outPath != None:
        ETUtils.write(corpus, outPath)
    return ET.ElementTree(corpus)

def openOutFile(setName, outPath, fileType, fileTypes, outFiles, openFiles):
    if fileType in fileTypes:
        if fileType not in outFiles[setName]:
            if outPath.endswith(".tsv"):
                filePath = outPath
                if not os.path.exists(os.path.dirname(outPath)):
                    os.makedirs(os.path.dirname(outPath))
            else:
                filePath = os.path.join(outPath, setName + "_" + fileType + ".tsv")
                if not os.path.exists(outPath):
                    os.makedirs(outPath)
            if filePath not in openFiles:
                print >> sys.stderr, "Opening output file", filePath
                openFiles[filePath] = open(filePath, "wt")
            outFiles[setName][fileType] = openFiles[filePath]
        return outFiles[setName][fileType]
    return None

def exportChemProtPredictions(xml, outPath, mode="predictions", setNames=None):
    assert mode in ("predictions", "full")
    xml = ETUtils.ETFromObj(xml)
    if mode == "predictions":
        fileTypes = ["predictions"]
    else:
        fileTypes = ["abstracts", "entities", "relations"]
    #with open(outPath, "wt") as f
    outFiles = {}
    openFiles = {}
    for document in xml.getiterator("document"):
        docId = document.get("origId")
        setName = document.get("set")
        if setNames != None:
            setName = setNames.get(setName, setName)
        if setName not in outFiles:
            outFiles[setName] = {}
        outFile = openOutFile(setName, outPath, "abstracts", fileTypes, outFiles, openFiles)
        if outFile != None:
            title, abstract = document.get("text").split("\t")
            outFile.write("\t".join([docId, title, abstract]))  
        entityById = {}
        for entity in document.getiterator("entity"):
            outFile = openOutFile(setName, outPath, "entities", fileTypes, outFiles, openFiles)
            if outFile != None:
                eType = entity.get("type")
                if "normalized" in entity:
                    eType += "-Y" if bool(entity.get("normalized")) else "-N"
                offset = Range.charOffsetToSingleTuple(entity.get("charOffset"))
                outFile.write("\t".join([docId, entity.get("origId"), eType, str[offset[0]], str[offset[1]], entity.get("text")]))
            assert entity.get("id") not in entityById
            entityById[entity.get("id")] = entity
        for interaction in document.getiterator("interaction"):
            e1 = entityById[interaction.get("e1")]
            e2 = entityById[interaction.get("e2")]
            outFile = openOutFile(setName, outPath, "relations", fileTypes, outFiles, openFiles)
            if outFile != None:
                evaluated = "X"
                if "evaluated" in interaction:
                    evaluated = "Y" if bool(entity.get("evaluated")) else "N"
                outFile.write("\t".join([docId, interaction.get("type"), evaluated, interaction.get("relType"), "Arg1:" + e1.get("origId"), "Arg2:" + e2.get("origId")]) + "\n")
            outFile = openOutFile(setName, outPath, "predictions", fileTypes, outFiles, openFiles)
            if outFile != None:
                outFile.write("\t".join([docId, interaction.get("type"), "Arg1:" + e1.get("origId"), "Arg2:" + e2.get("origId")]) + "\n")
    print >> sys.stderr, "Closing output files"
    for f in openFiles.values():
        f.close()
    return xml 