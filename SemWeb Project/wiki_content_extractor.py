import requests
import time
import logging
from rdflib import Graph, Literal, URIRef, Namespace, OWL, XSD, BNode
from rdflib.namespace import RDF, RDFS, FOAF, DCTERMS
import json
from urllib.parse import quote, unquote
import re
from abc import ABC, abstractmethod
import mwparserfromhell

class WikiContentExtractor(ABC):
    def __init__(self, base_namespace, resource_namespace, wiki_suffix):
        # Define namespaces
        self.base = Namespace(base_namespace)
        self.pokemon = Namespace("http://example.org/pokemon/")
        self.wiki = Namespace("https://bulbapedia.bulbagarden.net/wiki/")
        self.resource = Namespace(resource_namespace)
        self.wiki_suffix = wiki_suffix
        self.base_api_url = "https://bulbapedia.bulbagarden.net/w/api.php"
        self.headers = {
            'User-Agent': 'PokemonRDFEnricher/1.0 (contact@example.com)'
        }

    def get_wiki_content(self, name):
        """Get detailed wiki page content including wikitext, templates, images, and links"""
        params = {
            "action": "parse",
            "format": "json",
            "page": f"{name}_{self.wiki_suffix}",
            "prop": "wikitext|templates|images|links|categories|sections|properties|externallinks|iwlinks",
            "redirects": 1,
            "disablelimitreport": 1
        }

        try:
            response = requests.get(
                self.base_api_url,
                params=params,
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error retrieving wiki content for {name}: {str(e)}")
            return None

    def parse_infobox(self, wikitext):
        """Parse infobox template from wikitext"""
        parsed = mwparserfromhell.parse(wikitext)
        infobox_data = {}
        
        for template in parsed.filter_templates():
            template_name = str(template.name).strip().lower()
            if 'infobox' in template_name:
                for param in template.params:
                    name = str(param.name).strip()
                    value = str(param.value).strip()
                    if value and not value.startswith('<!--'):
                        # Clean up the value by removing wiki markup
                        value = re.sub(r'<.*?>', '', value)  # Remove HTML tags
                        value = re.sub(r'\[\[(?:[^|\]]*\|)?([^\]]+)\]\]', r'\1', value)  # Extract link text
                        value = re.sub(r"'{2,}", '', value)  # Remove bold/italic markers
                        value = value.replace('\n', ' ').strip()
                        infobox_data[name] = value
        
        return infobox_data

    def extract_sections(self, parse_data):
        """Extract section content from parse data"""
        sections = {}
        if 'sections' in parse_data:
            for section in parse_data['sections']:
                section_title = section.get('line', '').strip()
                if section_title:
                    sections[section_title] = {
                        'level': section.get('level', 0),
                        'number': section.get('number', ''),
                        'index': section.get('index', '')
                    }
        return sections

    def create_wiki_content_graph(self, names, resource_type):
        """Create RDF graph with detailed wiki content"""
        g = Graph()
        
        # Bind namespaces
        g.bind('base', self.base)
        g.bind('pokemon', self.pokemon)
        g.bind('wiki', self.wiki)
        g.bind('resource', self.resource)
        g.bind('owl', OWL)
        g.bind('foaf', FOAF)
        g.bind('dcterms', DCTERMS)
        
        for name in names:
            logging.info(f"Processing {name}...")
            
            # Create URIs
            uri_name = name.replace(' ', '_')
            resource_uri = self.resource[quote(uri_name)]
            wiki_uri = self.wiki[quote(f"{name}_{self.wiki_suffix}")]
            
            # Get wiki content
            content = self.get_wiki_content(name)
            if content and 'parse' in content:
                parse_data = content['parse']
                
                # Add basic type information
                g.add((resource_uri, RDF.type, resource_type))
                g.add((resource_uri, RDFS.label, Literal(name)))
                g.add((wiki_uri, RDF.type, self.pokemon.WikiPage))
                g.add((wiki_uri, self.pokemon.describes, resource_uri))
                
                # Add templates
                if 'templates' in parse_data:
                    for template in parse_data['templates']:
                        template_name = template.get('*', '').replace(' ', '_')
                        template_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/Template:{quote(template_name, safe='')}")
                        g.add((wiki_uri, self.pokemon.hasTemplate, template_uri))
                
                # Add images
                if 'images' in parse_data:
                    for image in parse_data['images']:
                        if not image.startswith('File:'):
                            continue
                        image_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/{quote(image, safe='')}")
                        g.add((wiki_uri, FOAF.depiction, image_uri))
                
                # Add links
                if 'links' in parse_data:
                    for link in parse_data['links']:
                        link_name = link.get('*', '').replace(' ', '_')
                        if link_name:
                            safe_link = quote(link_name, safe='')
                            link_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/{safe_link}")
                            g.add((wiki_uri, DCTERMS.references, link_uri))
                
                # Add external links
                if 'externallinks' in parse_data:
                    for ext_link in parse_data['externallinks']:
                        g.add((wiki_uri, RDFS.seeAlso, URIRef(ext_link)))
                
                # Add categories
                if 'categories' in parse_data:
                    for category in parse_data['categories']:
                        safe_category = quote(str(category), safe='')
                        category_uri = URIRef(f"https://bulbapedia.bulbagarden.net/wiki/Category:{safe_category}")
                        g.add((wiki_uri, DCTERMS.subject, category_uri))
                
                # Add sections
                sections = self.extract_sections(parse_data)
                for section_title, section_data in sections.items():
                    section_node = BNode()
                    g.add((wiki_uri, self.pokemon.hasSection, section_node))
                    g.add((section_node, RDFS.label, Literal(section_title)))
                    g.add((section_node, self.pokemon.sectionLevel, Literal(section_data['level'])))
                    g.add((section_node, self.pokemon.sectionNumber, Literal(section_data['number'])))
                
                # Parse infobox if wikitext is available
                if 'wikitext' in parse_data:
                    wikitext = parse_data['wikitext'].get('*', '')
                    infobox_data = self.parse_infobox(wikitext)
                    for key, value in infobox_data.items():
                        # Clean up the key name
                        clean_key = re.sub(r'[^a-zA-Z0-9]', '', key)
                        if clean_key:
                            g.add((resource_uri, self.pokemon[f"infobox_{clean_key}"], Literal(value)))
                    
                    # Store the raw wikitext for reference
                    g.add((wiki_uri, self.pokemon.wikitext, Literal(wikitext)))
            
            time.sleep(1)  # Be nice to the API
        
        return g

    @abstractmethod
    def load_names(self, ttl_file):
        """Load names from TTL file"""
        pass

class PokemonContentExtractor(WikiContentExtractor):
    def __init__(self):
        super().__init__(
            "http://example.org/pokemon/pokemon/",
            "http://example.org/pokemon/resource/",
            "(Pokémon)"
        )

    def load_names(self, ttl_file):
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        # Look for resources that are described by wiki pages
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

class MoveContentExtractor(WikiContentExtractor):
    def __init__(self):
        super().__init__(
            "http://example.org/pokemon/moves/",
            "http://example.org/pokemon/resource/moves/",
            "(move)"
        )

    def load_names(self, ttl_file):
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        # Look for resources that are described by wiki pages
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

class AbilityContentExtractor(WikiContentExtractor):
    def __init__(self):
        super().__init__(
            "http://example.org/pokemon/abilities/",
            "http://example.org/pokemon/resource/abilities/",
            "(ability)"
        )

    def load_names(self, ttl_file):
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        # Look for resources that are described by wiki pages
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

class LocationContentExtractor(WikiContentExtractor):
    def __init__(self):
        super().__init__(
            "http://example.org/pokemon/locations/",
            "http://example.org/pokemon/resource/locations/",
            ""  # Empty suffix since location pages don't have a consistent suffix
        )

    def get_wiki_content(self, name):
        """Get detailed wiki page content including wikitext, templates, images, and links"""
        # For locations, we need to append (location) to the page name
        params = {
            "action": "parse",
            "format": "json",
            "page": f"{name} (location)",  # Append (location) here
            "prop": "wikitext|templates|images|links|categories|sections|properties|externallinks|iwlinks",
            "redirects": 1,
            "disablelimitreport": 1
        }

        try:
            response = requests.get(
                self.base_api_url,
                params=params,
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error retrieving wiki content for {name}: {str(e)}")
            return None

    def load_names(self, ttl_file):
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        # Look for resources that are described by wiki pages
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

class RegionContentExtractor(WikiContentExtractor):
    def __init__(self):
        super().__init__(
            "http://example.org/pokemon/regions/",
            "http://example.org/pokemon/resource/regions/",
            ""  # Empty suffix since region pages don't have a consistent suffix
        )

    def get_wiki_content(self, name):
        """Get detailed wiki page content including wikitext, templates, images, and links"""
        # For regions, we need to append (region) to the page name
        params = {
            "action": "parse",
            "format": "json",
            "page": f"{name} (region)",  # Append (region) here
            "prop": "wikitext|templates|images|links|categories|sections|properties|externallinks|iwlinks",
            "redirects": 1,
            "disablelimitreport": 1
        }

        try:
            response = requests.get(
                self.base_api_url,
                params=params,
                headers=self.headers,
                timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logging.error(f"Error retrieving wiki content for {name}: {str(e)}")
            return None

    def load_names(self, ttl_file):
        g = Graph()
        g.parse(ttl_file, format='turtle')
        names = set()
        # Look for resources that are described by wiki pages
        for s, p, o in g.triples((None, self.pokemon.describes, None)):
            if isinstance(o, URIRef):
                name = str(o).split('/')[-1].replace('_', ' ')
                if name:
                    names.add(name)
        return names

def extract_wiki_content(extractor, input_file, output_file, resource_type):
    """Extract detailed wiki content for entities"""
    print(f"\nStarting {resource_type} wiki content extraction...")
    
    # Load names
    names = extractor.load_names(input_file)
    print(f"Loaded {len(names)} names")
    
    # Create graph with wiki content
    g = extractor.create_wiki_content_graph(names, resource_type)
    
    # Save the enriched graph
    g.serialize(destination=output_file, format='turtle')
    print(f"Wiki content saved to {output_file}")

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s',
        filename='wiki_content_extraction.log'
    )

    base_path = r"C:\Users\DELL\Desktop\M2\Semantic Web Redoubl\Knowledge Graph Pokemn_AliReda12"
    
    # Process Pokémon
    pokemon_extractor = PokemonContentExtractor()
    extract_wiki_content(
        pokemon_extractor,
        f"{base_path}\\Data\\pokemon_enriched.ttl",
        f"{base_path}\\Here\\pokemon_wiki_content.ttl",
        pokemon_extractor.pokemon.PokemonResource
    )
    
    # Process moves
    move_extractor = MoveContentExtractor()
    extract_wiki_content(
        move_extractor,
        f"{base_path}\\Data\\moves_enriched.ttl",
        f"{base_path}\\Here\\moves_wiki_content.ttl",
        move_extractor.pokemon.MoveResource
    )
    
    # Process abilities
    ability_extractor = AbilityContentExtractor()
    extract_wiki_content(
        ability_extractor,
        f"{base_path}\\Data\\abilities_with_i18n.ttl",
        f"{base_path}\\Here\\abilities_wiki_content.ttl",
        ability_extractor.pokemon.AbilityResource
    )
    
    # Process locations
    location_extractor = LocationContentExtractor()
    extract_wiki_content(
        location_extractor,
        f"{base_path}\\Data\\locations_enriched.ttl",
        f"{base_path}\\Here\\locations_wiki_content.ttl",
        location_extractor.pokemon.LocationResource
    )
    
    # Process regions
    region_extractor = RegionContentExtractor()
    extract_wiki_content(
        region_extractor,
        f"{base_path}\\Data\\regions_enriched.ttl",
        f"{base_path}\\Here\\regions_wiki_content.ttl",
        region_extractor.pokemon.RegionResource
    )
