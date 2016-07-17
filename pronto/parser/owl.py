
import functools
import os
import multiprocessing
import lxml.etree as etree

from pronto.parser import Parser
from pronto.relationship import Relationship
import pronto.utils


"""
NS = {xmlns:"http://purl.obolibrary.org/obo/uo.owl#"
      xml:base="http://purl.obolibrary.org/obo/uo.owl"
      xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
      xmlns:owl="http://www.w3.org/2002/07/owl#"
      xmlns:oboInOwl="http://www.geneontology.org/formats/oboInOwl#"
      xmlns:xsd="http://www.w3.org/2001/XMLSchema#"
      xmlns:uo="http://purl.obolibrary.org/obo/uo#"
      xmlns:rdfs="http://www.w3.org/2000/01/rdf-schema#"
      xmlns:obo="http://purl.obolibrary.org/obo/"}
"""

"""
{'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
 'rdfs': 'http://www.w3.org/2000/01/rdf-schema#',
  None: 'http://purl.obolibrary.org/obo/doid.owl#',
  'xsd': 'http://www.w3.org/2001/XMLSchema#',
  'obo': 'http://purl.obolibrary.org/obo/',
  'doid': 'http://purl.obolibrary.org/obo/doid#',
  'oboInOwl': 'http://www.geneontology.org/formats/oboInOwl#',
  'owl': 'http://www.w3.org/2002/07/owl#'}
"""



class _OwlXMLClassifier(multiprocessing.Process):

    def __init__(self, queue, results, nsmap):

        super(_OwlXMLClassifier, self).__init__()

        self.queue = queue
        self.results = results
        self.nsmap = {'rdf': 'http://www.w3.org/1999/02/22-rdf-syntax-ns#',
                      'rdfs': 'http://www.w3.org/2000/01/rdf-schema#'}

    def run(self):

        while True:

            term = self.queue.get()


            if term is None:
                #self.queue.task_done()
                break

            classified_term = self._classify(etree.fromstring(term))

            if classified_term:
                self.results.put(classified_term)

    def _classify(self, term):
        """
        Map raw information extracted from each owl Class.

        The raw data (in an etree.Element object) is extracted to a proper
        dictionnary containing a Term referenced by its id, which is then
        used to update :attribute:terms

        Todo:
            * Split into smaller methods to lower code complexity.
        """

        nspaced = functools.partial(pronto.utils.explicit_namespace, nsmap=self.nsmap)
        accession = functools.partial(pronto.utils.format_accession, nsmap=self.nsmap)

        if not term.attrib:
           return {}

        tid = accession(term.get(nspaced('rdf:about')))

        term_dict = {'name':'', 'relations': {}, 'desc': ''}

        translator = [
            {'hook': lambda c: c.tag == nspaced('rdfs:label'),
             'callback': lambda c: c.text,
             'dest': 'name',
             'action': 'store'
            },
            {
             'hook': lambda c: c.tag == nspaced('rdfs:subClassOf') \
                               and nspaced('rdf:resource') in c.attrib.keys(),
             'callback': lambda c: accession(c.get(nspaced('rdf:resource')) or c.get(nspaced('rdf:about'))),
             'dest': 'relations',
             'action': 'list',
             'list_to': 'is_a',
            },
            {'hook': lambda c: c.tag == nspaced('rdfs:comment'),
             'callback': lambda c: pronto.utils.parse_comment(c.text),
             'action': 'update'
            }
        ]

        for child in term.iterchildren():

            for rule in translator:

                if rule['hook'](child):

                    if rule['action'] == 'store':
                        term_dict[rule['dest']] = rule['callback'](child)

                    elif rule['action'] == 'list':

                        if not term_dict[rule['dest']]:
                            term_dict[rule['dest']][rule['list_to']] = []

                        term_dict[rule['dest']][rule['list_to']].append(rule['callback'](child))


                    elif rule['action'] == 'update':
                        term_dict.update(rule['callback'](child))


                    break

        #if ':' in tid: #remove administrative classes
        return (tid, term_dict)#{tid: pronto.term.Term(tid, **term_dict)}
        #else:
        #    return {}






class OwlXMLParser(Parser):
    """A parser for the owl xml format.
    """

    def __init__(self):
        super(OwlXMLParser, self).__init__()
        self._tree = None
        self._ns = {}
        self.extensions = ('.owl', '.xml', '.ont')

    def hook(self, *args, **kwargs):
        """Returns True if the file is an Owl file (extension is .owl)"""
        if 'path' in kwargs:
            return os.path.splitext(kwargs['path'])[1] in self.extensions

    def read(self, stream):
        """
        Parse the content of the stream
        """

        self.init_workers(_OwlXMLClassifier, self._ns)

        events = ("start", "end", "start-ns")

        for event, element in etree.iterparse(stream, huge_tree=True, events=events):

            if element is None:
                break

            if event == "start-ns":
                self._ns.update({element[0]:element[1]})

            elif element.tag==pronto.utils.explicit_namespace('owl:imports', self._ns) and event=='end':
                self.imports.append(element.attrib[pronto.utils.explicit_namespace('rdf:resource', self._ns)])

            elif element.tag==pronto.utils.explicit_namespace('owl:Class', self._ns) and event=='end':
                self._rawterms.put(etree.tostring(element))




    def makeTree(self, pool):
        """
        Maps :function:_classify to each term of the file via a ThreadPool.

        Once all the raw terms are all classified, the :attrib:terms dictionnary
        gets updated.

        Arguments:
            pool (Pool): a pool of workers that is used to map the _classify
                function on the terms.
        """
        #terms_elements = self._tree.iterfind('./owl:Class', self._ns)
        #for t in pool.map(self._classify, self._elements):
        #    self.terms.update(t)

        while self._terms.qsize() > 0: #or self._rawterms.qsize() > 0:
            tid, d = self._terms.get()
            d['relations'] = { Relationship(k):v for k,v in d.items() }

            self.terms[tid] = pronto.term.Term(tid, **d)

        #self.shut_workers()

    def manage_imports(self):
        pass
        #nspaced = functools.partial(pronto.utils.explicit_namespace, nsmap=self._ns)
        #for imp in self._tree.iterfind('./owl:Ontology/owl:imports', self._ns):
        #    path = imp.attrib[nspaced('rdf:resource')]
        #    if path.endswith('.owl'):
        #        self.imports.append(path)

    def metanalyze(self):
        """
        Extract metadata from the headers of the owl file.

        Todo:
            * Implement that method !
        """
        pass


OwlXMLParser()
