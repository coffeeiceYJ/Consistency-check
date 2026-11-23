import java.nio.file.*;
import java.util.*;
import com.fasterxml.jackson.databind.*;
import edu.kit.kastel.sdq.lissa.ratlr.knowledge.Element;
import edu.kit.kastel.sdq.lissa.ratlr.elementstore.TargetElementStore;
import edu.kit.kastel.sdq.lissa.ratlr.elementstore.strategy.CosineSimilarity;
import edu.kit.kastel.sdq.lissa.ratlr.configuration.ModuleConfiguration;
import edu.kit.kastel.sdq.lissa.ratlr.utils.Pair;

/** Adapter compiled against the original LiSSA JAR. No upstream source copied. */
public class LissaBridge {
    static Element element(JsonNode n) {
        return new Element(n.get("id").asText(), "text", n.get("text").asText(), 0, (Element)null, true);
    }
    static float[] vector(JsonNode n) {
        float[] v=new float[n.size()];
        for(int i=0;i<v.length;i++) v[i]=(float)n.get(i).asDouble();
        return v;
    }
    public static void main(String[] args) throws Exception {
        var mapper=new ObjectMapper();
        var input=mapper.readTree(Path.of(args[0]).toFile());
        var content=new ArrayList<Pair<Element,float[]>>();
        for(var n:input.get("targets")) content.add(new Pair<>(element(n),vector(n.get("vector"))));
        var config=new ModuleConfiguration("cosine_similarity",Map.of("max_results",input.get("top_k").asText()));
        var store=new TargetElementStore(content,new CosineSimilarity(config));
        var links=new ArrayList<Map<String,Object>>();
        for(var n:input.get("sources")) {
            for(var hit:store.findSimilarWithDistances(new Pair<>(element(n),vector(n.get("vector"))))) {
                if(Float.isFinite(hit.second()) && hit.second()>0) links.add(Map.of(
                    "requirement_id",n.get("id").asText(),"model_id",hit.first().getIdentifier(),"score",hit.second()));
            }
        }
        mapper.writeValue(Path.of(args[1]).toFile(),links);
    }
}
